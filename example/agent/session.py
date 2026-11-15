"""Прогон одного кейса агентом GigaCat.

Поднимает headless-инстанс (`client.agent_session`), отправляет user_phrase,
пишет события в raw.log; при уточняющем вопросе кейс дропается, при
отсутствии артефакта — дожимается сообщениями «Продолжить».

`client.*` импортируется лениво: в этом чекауте пакета нет, и работает
только `--validate` (см. AGENTS.md).
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..cases import makedirs
from ..config import case_runs_dir
from ..evaluate import evaluate_case
from ..logs import RawLogWriter, extract_questions, record_event
from .events import (
    agent_produced_artifact,
    collect_skills_tools,
    collect_tokens,
    extract_artifacts,
    extract_error,
    paths_from_tool_output,
)

# Сколько раз шлём «Продолжить», если агент так и не создал артефакт.
MAX_CONTINUATIONS = 7


async def run_case(
    case,
    runs_root: Path,
    semaphore: asyncio.Semaphore,
    model: Optional[str] = None,
    run_num: int = 1,
):
    """Поднять инстанс, выполнить запрос и собрать результат по одному кейсу."""
    from client.agent_session import AgentSession
    from client.agent_client import AgentResult

    runs_dir = case_runs_dir(runs_root, run_num, case.id)
    work_dir = runs_dir / "work_dir"
    makedirs(work_dir, runs_dir)

    # Чистим work_dir и прежние рендеры, чтобы не подмешать старые файлы.
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    render_dir = runs_dir / "render_artifact"
    if render_dir.exists():
        shutil.rmtree(render_dir, ignore_errors=True)

    output_paths: List[str] = []
    collected_questions: List[Dict[str, Any]] = []

    async with semaphore:
        with AgentSession(work_dir=str(work_dir), model=model) as session:
            result: AgentResult = AgentResult(session_id="")

            with RawLogWriter(runs_dir / "raw.log") as raw:
                # Сетевой трафик прокси пишем в тот же raw.log по мере прохода.
                if session.instance and session.instance.proxy:
                    session.instance.proxy.attach_logger(raw.write_api_trace)

                session.create_session(title=f"run-{case.id}")

                def _consume_events(session, message: str) -> None:
                    """Пройти действия агента по сообщению и собрать события.

                    Останавливаемся, если агент задал уточняющий вопрос.
                    """
                    nonlocal output_paths, collected_questions
                    for ev in session.actions(message, dangerously_skip_permissions=True):
                        result.events.append(ev)
                        result.text_outputs += ev.text_outputs
                        result.reasonings += ev.reasonings
                        result.tool_calls += ev.tool_calls
                        for tc in ev.tool_calls:
                            output_paths += paths_from_tool_output(tc)
                        for art in getattr(ev, "artifacts", None) or []:
                            result.artifacts.append(art)
                        if ev.type == "session.error":
                            result.error = extract_error(ev)
                        # Вопрос агента (question.asked): без ответа сессия не уйдёт
                        # в idle, так что ловим его здесь и дропаем кейс.
                        collected_questions += extract_questions(ev)
                        record_event(ev, raw)
                        if collected_questions:
                            break

                # В тест-кейсе ответов пользователя нет, так что это время агента
                # от отправки промпта до перехода сессии в idle.
                t_start = time.monotonic()

                _consume_events(session, case.user_phrase)

                # Если агент закончил без артефакта, дожимаем «Продолжить»,
                # но не более MAX_CONTINUATIONS раз. При терминальной ошибке
                # это бессмысленно — фиксируем кейс как упавший.
                continuations = 0
                while (
                    not collected_questions
                    and not result.error
                    and continuations < MAX_CONTINUATIONS
                    and not agent_produced_artifact(work_dir, output_paths)
                ):
                    continuations += 1
                    print(f"[run:{case.id}] no artifact yet, sending 'Продолжить' "
                          f"({continuations}/{MAX_CONTINUATIONS})")
                    _consume_events(session, "Продолжить")
                if result.error:
                    print(f"[run:{case.id}] agent finished with error: {result.error}")

                t_end = time.monotonic()
                agent_time_sec = (t_end - t_start) if t_end >= t_start else 0.0
                tokens = collect_tokens(result.events)

    skills, tools = collect_skills_tools(result.events)
    artifacts = list(dict.fromkeys(
        extract_artifacts(result, work_dir, output_paths, case.artifact_type)))
    abs_artifacts = [
        str(p if p.is_absolute() else work_dir / p) for p in map(Path, artifacts)
    ]
    return await evaluate_case(
        case, runs_root, abs_artifacts, skills, tools, collected_questions,
        run_num=run_num, agent_time_sec=agent_time_sec, tokens=tokens,
        continuations=continuations,
    )