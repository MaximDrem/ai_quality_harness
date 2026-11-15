"""Оценка артефактов кейса: рендер, функциональные проверки, судья-LLM.

Общий путь для полного прогона (артефакты создаёт агент) и `--validate`
(артефакт уже готов). Судья-LLМ импортируется лениво: конфиг клиента
читает env в момент импорта, а `.env` загружается в CLI до этого.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cases import build_check_list, load_common_checks, load_test_cases, makedirs
from .checks.functional import run_functional_checks
from .config import RENDER_SUPPORTED, WORK_DIRS_DIR, case_runs_dir
from .models import Check, CheckResult, RunResult, TestCase
from .logs import write_case_log
from .render.office import render_office_file


def render_artifacts(artifacts: List[str], run_dir: Path, expected_type: str) -> List[str]:
    """Отрендерить артефакты данного типа в PNG (в run_dir/render_artifact)."""
    photos: List[str] = []
    if expected_type not in RENDER_SUPPORTED:
        return photos
    out_dir = run_dir / "render_artifact"
    for art in artifacts:
        p = Path(art)
        if p.suffix.lower() != expected_type:
            continue
        res = render_office_file(p, out_dir)
        if res.get("correct"):
            photos.extend(res.get("photos_path", []))
    return photos


async def _judge_case(case: TestCase, artifacts: List[str], photos: List[str]) -> List[CheckResult]:
    """Прогнать text/vlm-проверки кейса судьёй-LLM.

    Пакет `judge` импортируется лениво, чтобы к этому моменту `.env` уже
    был загружен и конфиг клиента собрался с нужными переменными.
    """
    from .judge import runner as judge_runner
    return await judge_runner.run_judge(case, artifacts, photos)


async def evaluate_case(
    case: TestCase,
    runs_root: Path,
    abs_artifacts: List[str],
    skills: List[str],
    tools: List[str],
    questions: Optional[List[Dict[str, Any]]] = None,
    run_num: int = 1,
    agent_time_sec: float = 0.0,
    tokens: Optional[Dict[str, int]] = None,
    continuations: int = 0,
) -> RunResult:
    """Отрендерить артефакты, записать лог, прогнать проверки и собрать RunResult.

    Общая для прогона агентом и проверки готового файла. Каждый прогон пишется
    в `<runs_root>/run_<run_num>/<id_case>/`.
    """
    runs_dir = case_runs_dir(runs_root, run_num, case.id)
    makedirs(runs_dir)
    questions = questions or []
    tokens = tokens or {}

    if questions:
        # Уточняющий вопрос — кейс не доведён до результата: дропаем без рендера
        # и судьи, в xlsx помечаем как «Вопрос агента».
        drop_reason = "Агент попал в вопрос: " + "; ".join(
            (q.get("question") if isinstance(q, dict) else str(q)) or "?"
            for q in questions
        )
        return RunResult(
            test_case=case,
            questions=questions,
            dropped=True,
            drop_reason=drop_reason,
            agent_time_sec=agent_time_sec,
            tokens=tokens,
            continuations=continuations,
        )

    render_photos = render_artifacts(abs_artifacts, runs_dir, case.artifact_type)

    # Сводный лог кейса: check_list, скилы/тулы/артефакты, фото, вопросы, метрики.
    write_case_log(runs_dir, case, skills, tools, abs_artifacts, render_photos, questions,
                   agent_time_sec=agent_time_sec, tokens=tokens, continuations=continuations)

    # Сначала функциональные проверки (кодом), потом судья-LLM.
    functional_results = run_functional_checks(case, abs_artifacts, render_photos)
    llm_results = await _judge_case(case, abs_artifacts, render_photos)
    all_check_results = functional_results + llm_results

    print(f"[run:{case.id}] functional={len(functional_results)} judge_checks={len(llm_results)}")
    print(f"[run:{case.id}] skills={skills}")
    print(f"[run:{case.id}] tools={tools}")
    print(f"[run:{case.id}] artifacts={abs_artifacts}")
    print(f"[run:{case.id}] render_photos={render_photos}")
    print(f"[run:{case.id}] questions={len(questions)}")
    print(f"[run:{case.id}] agent_time_sec={agent_time_sec:.3f} tokens={tokens} continuations={continuations}")

    return RunResult(
        test_case=case,
        skills=skills,
        tools=tools,
        artifacts=abs_artifacts,
        render_photos=render_photos,
        questions=questions,
        check_results=all_check_results,
        agent_time_sec=agent_time_sec,
        tokens=tokens,
        continuations=continuations,
    )


def with_universal_checks(case: TestCase) -> TestCase:
    """Собрать итоговый чек-лист кейса для полного прогона агента.

    Общие проверки по типу файла (`checks` из checks/common/<тип>.yaml) +
    индивидуальные из data/<case_id>.yaml + универсальный блок (`universal`);
    строки, уже присутствующие в чек-листе, повторно не добавляются.
    """
    return replace(case, check_list=build_check_list(case, include_universal=True))


def case_for_validate(
    artifact_path: str,
    case_id: Optional[str],
    checks: Optional[str],
) -> TestCase:
    """Собрать TestCase для проверки готового файла (id кейса = "example").

    Чек-лист берётся по расширению файла из блока `universal` в
    `checks/common/<тип>.yaml`; `--checks` или `--case` его переопределяют.
    Строки `--checks` считаются text-проверками (по одной на строку).
    """
    path = Path(artifact_path)
    suffix = path.suffix.lower()

    cases = load_test_cases()
    base: Optional[TestCase] = None
    if case_id:  # конкретный кейс из data/*.yaml
        base = next((c for c in cases if c.id == case_id), None)
    else:
        base = next((c for c in cases if c.artifact_type == suffix), None)  # для user_phrase

    if checks:
        check_list = [
            Check(text=ln.strip(), kind="text")
            for ln in checks.splitlines() if ln.strip()
        ]
    elif case_id is not None and base is not None:
        # Как в полном прогоне: общие + индивидуальные + universal.
        check_list = build_check_list(base, include_universal=True)
    else:
        check_list = load_common_checks(suffix)["universal"]

    return TestCase(
        id="example",
        user_phrase="Сделай презентацию об агенте СберКот",
        skill=base.skill if base else "",
        artifact_type=base.artifact_type if base else suffix,
        check_list=check_list,
    )


async def validate_artifact(case: TestCase, artifact_path: str, runs_root: Path) -> RunResult:
    """Проверить готовый артефакт без запуска агента: рендер + проверки."""
    abs_artifacts = [str(Path(artifact_path).resolve())]
    return await evaluate_case(case, runs_root, abs_artifacts, [], [], [])


def list_models() -> List[str]:
    """Поднять headless-инстанс и вернуть список доступных моделей.

    Требует `client/` (в этом чекауте отсутствует — см. AGENTS.md).
    """
    from client.agent_session import AgentSession

    from .agent.cookies import refresh_cookies

    # Свежие куки — иначе отдаётся дефолтный каталог моделей.
    refresh_cookies()

    work_dir = WORK_DIRS_DIR / "_models_probe"
    makedirs(work_dir)
    names: List[str] = []
    with AgentSession(work_dir=str(work_dir)) as session:
        provs = session.client.http.get("/config/providers")
        for prov in provs.get("providers", []):
            models = prov.get("models") or {}
            for mid in models:
                names.append(f"{prov.get('id')}/{mid}")
    return names