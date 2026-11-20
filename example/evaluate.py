"""Оркестрация оценки одного кейса.

Выбирает артефакты, полностью обрабатывает ArtifactContext через processors,
затем запускает функциональные и LLM-проверки и собирает RunResult.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from .artifacts import validation_context
from .cases import (
    DEFAULT_SCENARIO,
    artifact_specs,
    build_check_list,
    load_test_cases,
    makedirs,
    validate_case_output,
)
from .checks.functional import run_functional_checks
from .config import CASES_DIR, WORK_DIRS_DIR, case_runs_dir
from .logs import write_case_log
from .models import Artifact, ArtifactContext, Check, CheckResult, RunResult, TestCase
from .processors import process_context


async def _judge_case(case: TestCase, context: ArtifactContext) -> List[CheckResult]:
    from .judge import runner as judge_runner

    return await judge_runner.run_judge(case, context)


def _missing_artifact_results(case: TestCase, context: ArtifactContext) -> List[CheckResult]:
    results: List[CheckResult] = []
    for spec in artifact_specs(case):
        if not spec.required or context.select([spec.name]):
            continue
        check = Check(
            text=f"Обязательный артефакт: {spec.name}",
            kind="function",
            metric="Артефакты",
            handler="path/artifact_required",
            artifacts=[spec.name],
        )
        results.append(
            CheckResult(
                check=check,
                success=False,
                reason=(
                    f"Обязательный {'финальный' if spec.is_final_result else 'вспомогательный'} "
                    f"артефакт {spec.name!r} не найден"
                ),
                source="—",
                artifact_path=spec.path or None,
            )
        )
    return results


def _result_metadata(
    case: TestCase,
    skills: List[str],
    context_usage: Dict[str, float],
    evaluate_agent: bool,
) -> Dict[str, Any]:
    actual_skill = skills[-1] if skills else ""
    return {
        "actual_skill": actual_skill,
        "routing_success": actual_skill == case.skill,
        "routing_applicable": evaluate_agent,
        "context_tokens": int(context_usage.get("tokens") or 0),
        "context_limit": int(context_usage.get("limit") or 0),
        "context_percent": float(context_usage.get("percent") or 0),
    }


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
    artifact_context: Optional[ArtifactContext] = None,
    context_usage: Optional[Dict[str, float]] = None,
    evaluate_agent: bool = True,
) -> RunResult:
    runs_dir = case_runs_dir(runs_root, run_num, case.id)
    makedirs(runs_dir)
    questions = questions or []
    tokens = tokens or {}
    context_usage = context_usage or {}
    context = artifact_context or ArtifactContext(
        items=[
            Artifact(
                path=str(Path(path).resolve()),
                role="final",
                name="final",
                artifact_type=Path(path).suffix.lower(),
                origin_path=str(Path(path).resolve()),
                processor=case.processor,
            )
            for path in abs_artifacts
        ]
    )
    for index, artifact in enumerate(context.items, start=1):
        if artifact.path:
            artifact.render_dir = str(
                runs_dir / "render_artifact" / f"{index:02d}_{artifact.role}_{artifact.name}"
            )
    supporting = context.paths(["supporting"])
    sources = context.paths(["source"])
    messages = context.select(["final_message"])
    final_message = messages[-1].text if messages else ""
    metadata = _result_metadata(case, skills, context_usage, evaluate_agent)

    if questions:
        reason = "Агент попал в вопрос: " + "; ".join(
            (question.get("question") if isinstance(question, dict) else str(question)) or "?"
            for question in questions
        )
        return RunResult(
            test_case=case,
            questions=questions,
            dropped=True,
            drop_reason=reason,
            agent_time_sec=agent_time_sec,
            tokens=tokens,
            continuations=continuations,
            supporting_artifacts=supporting,
            source_artifacts=sources,
            final_message=final_message,
            **metadata,
        )

    final_paths = context.paths(["final"])
    process_context(context)
    write_case_log(
        runs_dir,
        case,
        skills,
        tools,
        final_paths,
        [],
        questions,
        agent_time_sec=agent_time_sec,
        tokens=tokens,
        continuations=continuations,
        artifact_context=context,
    )

    missing_results = _missing_artifact_results(case, context)
    functional_results = run_functional_checks(case, context)
    llm_results = await _judge_case(case, context)
    all_results = missing_results + functional_results + llm_results
    final_photos = [
        photo
        for artifact in context.select(["final"])
        for photo in artifact.rendered_photos
    ]

    print(f"[run:{case.id}] functional={len(functional_results)} judge_checks={len(llm_results)}")
    print(f"[run:{case.id}] skills={skills}")
    print(f"[run:{case.id}] tools={tools}")
    print(f"[run:{case.id}] artifacts={final_paths}")
    print(f"[run:{case.id}] supporting_artifacts={supporting}")
    print(f"[run:{case.id}] render_photos={final_photos}")
    print(f"[run:{case.id}] questions={len(questions)}")
    print(f"[run:{case.id}] agent_time_sec={agent_time_sec:.3f} tokens={tokens} continuations={continuations}")
    print(f"[run:{case.id}] context={context_usage}")

    return RunResult(
        test_case=case,
        skills=skills,
        tools=tools,
        artifacts=final_paths,
        render_photos=final_photos,
        questions=questions,
        check_results=all_results,
        agent_time_sec=agent_time_sec,
        tokens=tokens,
        continuations=continuations,
        supporting_artifacts=supporting,
        source_artifacts=sources,
        final_message=final_message,
        **metadata,
    )


def with_universal_checks(case: TestCase) -> TestCase:
    result = replace(case, check_list=build_check_list(case, include_universal=True))
    validate_case_output(result)
    return result


def case_for_validate(
    artifact_path: str,
    case_id: Optional[str],
    checks: Optional[str],
    scenario: Optional[str] = None,
) -> TestCase:
    path = Path(artifact_path)
    suffix = path.suffix.lower()
    scenario_name = scenario or DEFAULT_SCENARIO
    cases = load_test_cases(scenario=scenario_name)
    if case_id:
        base = next((case for case in cases if case.id == case_id), None)
        if base is None:
            raise ValueError(f"Кейс {case_id!r} не найден в сценарии {scenario_name!r}")
    else:
        base = next((case for case in cases if case.artifact_type == suffix), None)

    if checks:
        check_list = [
            Check(text=line.strip(), kind="llm", input="text")
            for line in checks.splitlines()
            if line.strip()
        ]
    elif base is not None:
        source = base if case_id is not None else replace(base, check_list=[])
        check_list = build_check_list(
            source,
            include_universal=True,
            include_common=case_id is not None,
        )
    else:
        check_list = []

    if base is not None:
        return replace(base, id="example", check_list=check_list)
    return TestCase(
        id="example",
        user_phrase="",
        skill="",
        artifact_type=suffix,
        check_list=check_list,
        scenario=scenario_name,
        case_dir=str(CASES_DIR / scenario_name),
    )


async def validate_artifact(case: TestCase, artifact_path: str, runs_root: Path) -> RunResult:
    context = validation_context(case, artifact_path)
    return await evaluate_case(
        case,
        runs_root,
        context.paths(["final"]),
        [],
        [],
        [],
        artifact_context=context,
        evaluate_agent=False,
    )


def list_models(
    auth_mode: Optional[str] = None,
    skills_dir: Optional[str] = None,
) -> List[str]:
    from client import AgentSession

    kwargs: Dict[str, Any] = {"work_dir": str(WORK_DIRS_DIR / "_models_probe")}
    if auth_mode:
        kwargs["auth_mode"] = auth_mode
    if skills_dir:
        kwargs["skills_dir"] = Path(skills_dir).expanduser()
    makedirs(WORK_DIRS_DIR / "_models_probe")
    names: List[str] = []
    with AgentSession(**kwargs) as session:
        providers = session.client.http.get("/config/providers")
        for provider in providers.get("providers", []):
            for model_id in provider.get("models") or {}:
                names.append(f"{provider.get('id')}/{model_id}")
    return names
