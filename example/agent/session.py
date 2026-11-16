"""Выполнение кейса в AgentSession и потоковый сбор событий.

Передаёт вложения только в первом запросе, сохраняет raw.log и agent.log,
ожидает все обязательные финалы и передаёт ArtifactContext в evaluator.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..artifacts import ArtifactCollector
from ..cases import makedirs, resolve_attachment_paths
from ..config import case_runs_dir
from ..evaluate import evaluate_case
from ..logs import HumanLogWriter, RawLogWriter, extract_questions, record_event
from .events import (
    collect_context_usage,
    collect_skills_tools,
    collect_tokens,
    extract_error,
    paths_from_tool_input,
    paths_from_tool_output,
)

MAX_CONTINUATIONS = 7


async def run_case(
    case,
    runs_root: Path,
    semaphore: asyncio.Semaphore,
    model: Optional[str] = None,
    run_num: int = 1,
    auth_mode: Optional[str] = None,
    skills_dir: Optional[str] = None,
):
    from client import AgentResult, AgentSession

    runs_dir = case_runs_dir(runs_root, run_num, case.id)
    work_dir = runs_dir / "work_dir"
    makedirs(work_dir, runs_dir)
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    render_dir = runs_dir / "render_artifact"
    if render_dir.exists():
        shutil.rmtree(render_dir, ignore_errors=True)
    snapshot_dir = runs_dir / "intermediate_artifacts"
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir, ignore_errors=True)

    attachment_paths = resolve_attachment_paths(case)
    collector = ArtifactCollector(case, work_dir, snapshot_dir)
    output_paths: List[str] = []
    collected_questions: List[Dict[str, Any]] = []
    event_index = 0

    session_kwargs: Dict[str, Any] = {"work_dir": str(work_dir), "model": model}
    if auth_mode:
        session_kwargs["auth_mode"] = auth_mode
    if skills_dir:
        session_kwargs["skills_dir"] = Path(skills_dir).expanduser()

    async with semaphore:
        with AgentSession(**session_kwargs) as session:
            result: AgentResult = AgentResult(session_id="")
            with RawLogWriter(runs_dir / "raw.log") as raw, HumanLogWriter(runs_dir / "agent.log") as human_log:
                proxy = session.instance.proxy if session.instance else None
                attach_logger = getattr(proxy, "attach_logger", None)
                if callable(attach_logger):
                    attach_logger(raw.write_api_trace)
                session.create_session(title=f"run-{case.id}")

                def _consume_events(message: str, attachments: Optional[List[str]] = None) -> None:
                    nonlocal output_paths, collected_questions, event_index
                    kwargs: Dict[str, Any] = {"dangerously_skip_permissions": True}
                    if attachments:
                        kwargs["attachments"] = attachments
                    for event in session.actions(message, **kwargs):
                        event_index += 1
                        result.events.append(event)
                        result.text_outputs += event.text_outputs
                        result.reasonings += event.reasonings
                        result.tool_calls += event.tool_calls
                        for tool_call in event.tool_calls:
                            output_paths += paths_from_tool_input(tool_call)
                            output_paths += paths_from_tool_output(tool_call)
                        for artifact in getattr(event, "artifacts", None) or []:
                            result.artifacts.append(artifact)
                        if event.type == "session.error":
                            result.error = extract_error(event)
                        collected_questions += extract_questions(event)
                        collector.capture_event(event, event_index)
                        record_event(event, raw)
                        human_log.write_event(event)
                        if collected_questions:
                            break

                started = time.monotonic()
                _consume_events(case.user_phrase, attachment_paths)
                continuations = 0
                while (
                    not collected_questions
                    and not result.error
                    and continuations < MAX_CONTINUATIONS
                    and not collector.has_final(result, output_paths)
                ):
                    continuations += 1
                    print(f"[run:{case.id}] no artifact yet, sending 'Продолжить' "
                          f"({continuations}/{MAX_CONTINUATIONS})")
                    _consume_events("Продолжить")
                if result.error:
                    print(f"[run:{case.id}] agent finished with error: {result.error}")
                ended = time.monotonic()
                agent_time_sec = ended - started if ended >= started else 0.0
                tokens = collect_tokens(result.events)

    artifact_context = collector.finalize(result, output_paths)
    artifacts = artifact_context.paths(["final"])
    skills, tools = collect_skills_tools(result.events)
    context_usage = collect_context_usage(result.events)
    return await evaluate_case(
        case,
        runs_root,
        artifacts,
        skills,
        tools,
        collected_questions,
        run_num=run_num,
        agent_time_sec=agent_time_sec,
        tokens=tokens,
        continuations=continuations,
        artifact_context=artifact_context,
        context_usage=context_usage,
    )
