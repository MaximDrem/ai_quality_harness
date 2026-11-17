"""Оркестрация судейства: run_judge и посайдовые режимы.

Judge выбирает Artifact по YAML и поддерживает text, rendered_photos,
tool_calls и сериализованный raw_structure через необязательное поле input.
Профиль промптов и схемы выбирается по case.scenario во всех режимах.
Число запросов к vLLM ограничено JUDGE_MAX_CONCURRENCY.
В single_slide режиме FAIL агрегируется по слайдам. Результат записывается
в artifact.results, включая виртуальный final_message.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import List, Optional, Tuple

from ..config import JUDGE_TEXT_MAX_CHARS
from ..models import Artifact, ArtifactContext, Check, CheckResult, TestCase
from .client import JUDGE_MAX_CONCURRENCY, judge_images, judge_text


def _judger_error_result(check: Check, message: str) -> CheckResult:
    return CheckResult(
        check=check,
        success=False,
        reason=message,
        mode=check.mode,
        source="—",
        error=message,
    )


def _is_pass(verdict) -> bool:
    """Вердикт судьи по схеме — строка 'True'/'failed'."""
    return str(verdict.success).lower() == "true"


def _aggregate_slide_verdicts(
    check: Check,
    results: List[Tuple[Optional[str], object]],
    source: str,
) -> CheckResult:
    """Собрать посайдовые вердикты в один: FAIL, если упал хотя бы один слайд.

    `results` — пары (метка фрагмента «слайд N» или None для фрагмента без
    номера, вердикт судьи по этому фрагменту).
    """
    failed = [(label, v) for label, v in results if not _is_pass(v)]
    if not failed:
        return CheckResult(
            check=check,
            success=True,
            reason=f"Нарушений не найдено (проверено слайдов: {len(results)})",
            mode=check.mode,
            source=source,
        )
    parts = [f"{label}: {v.reason}" if label else v.reason for label, v in failed]
    reason = f"Нарушения на {len(failed)} из {len(results)} слайдов. " + " | ".join(parts)
    return CheckResult(
        check=check,
        success=False,
        reason=reason,
        mode=check.mode,
        source=source,
    )


async def _judge_by_slides(
    check: Check,
    user_instruction: Optional[str],
    photos: List[str],
    semaphore: asyncio.Semaphore,
    scenario: str,
) -> CheckResult:
    """Судить vlm-проверку по одному слайду за запрос."""
    async def _one(idx: int, photo: str) -> Tuple[str, object]:
        async with semaphore:
            verdict = await judge_images(
                image_paths=[photo],
                user_instruction=user_instruction,
                check_instruction=check.text,
                first_slide=idx,
                scenario=scenario,
            )
            return f"слайд {idx}", verdict

    results = await asyncio.gather(
        *(_one(i, p) for i, p in enumerate(photos, start=1))
    )
    return _aggregate_slide_verdicts(
        check, list(results), f"фото по одному: {len(photos)}")


async def _judge_text_by_slides(
    check: Check,
    user_instruction: Optional[str],
    text: str,
    semaphore: asyncio.Semaphore,
    scenario: str,
) -> CheckResult:
    """Судить text-проверку по одному слайду за запрос.

    Текст режется по маркерам «=== СЛАЙД N ===»; преамбула (шапка
    «--- файл ---») не судится.
    """
    # Чанки: (номер слайда | None для преамбулы, текст с маркером).
    chunks: List[Tuple[Optional[int], str]] = []
    parts = re.split(r"^=== СЛАЙД (\d+) ===\s*$", text, flags=re.M)
    preamble = parts[0].strip()
    if preamble and not re.fullmatch(r"(--- .* ---\s*)*", preamble):
        chunks.append((None, preamble))
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1].strip()
        if body:
            chunks.append((int(parts[i]), f"=== СЛАЙД {parts[i]} ===\n{body}"))

    async def _one(slide_no: Optional[int], body: str) -> Tuple[Optional[str], object]:
        async with semaphore:
            verdict = await judge_text(
                agent_text=body,
                user_instruction=user_instruction,
                check_instruction=check.text,
                scenario=scenario,
            )
            return (f"слайд {slide_no}" if slide_no is not None else None), verdict

    results = await asyncio.gather(*(_one(n, b) for n, b in chunks))
    return _aggregate_slide_verdicts(
        check, list(results), f"текст по слайдам: {len(chunks)}")


async def _judge_vlm(
    check: Check,
    user_instruction: Optional[str],
    photos: List[str],
    semaphore: asyncio.Semaphore,
    scenario: str,
) -> CheckResult:
    """Визуальная проверка: вся колода одним запросом или по одному слайду."""
    if not photos:
        return CheckResult(
            check=check,
            success=False,
            reason="Нет отрендеренных фото артефакта для визуальной проверки",
            mode=check.mode,
            source="—",
        )
    if check.single_slide:
        # Каждый слайд судится отдельным запросом.
        return await _judge_by_slides(check, user_instruction, photos, semaphore, scenario)
    # Вся колода одним запросом — для проверок, оценивающих её целиком.
    async with semaphore:
        verdict = await judge_images(
            image_paths=photos,
            user_instruction=user_instruction,
            check_instruction=check.text,
            scenario=scenario,
        )
    return CheckResult(
        check=check,
        success=_is_pass(verdict),
        reason=verdict.reason,
        mode=check.mode,
        source=f"фото: {len(photos)}",
    )


async def _judge_text(
    check: Check,
    user_instruction: Optional[str],
    artifacts: List[Artifact],
    semaphore: asyncio.Semaphore,
    text_override: Optional[str] = None,
    source_override: Optional[str] = None,
    scenario: str = "presentation",
) -> CheckResult:
    if text_override is None:
        chunks = []
        for artifact in artifacts:
            label = artifact.path or artifact.name
            chunks.append(f"--- {label} ---\n{artifact.text}")
        text = "\n\n".join(chunks)
    else:
        text = text_override
    if not text.strip():
        return CheckResult(
            check=check,
            success=False,
            reason="Не удалось извлечь текст из артефакта для текстовой проверки",
            mode=check.mode,
            source="—",
        )
    if check.single_slide and text_override is None:
        return await _judge_text_by_slides(check, user_instruction, text, semaphore, scenario)
    async with semaphore:
        verdict = await judge_text(
            agent_text=text[:JUDGE_TEXT_MAX_CHARS],
            user_instruction=user_instruction,
            check_instruction=check.text,
            scenario=scenario,
        )
    return CheckResult(
        check=check,
        success=_is_pass(verdict),
        reason=verdict.reason,
        mode=check.mode,
        source=source_override or f"текст: {len(text)} симв.",
    )


def _attach_result(check: Check, result: CheckResult, selected: List[Artifact], attribute: str) -> CheckResult:
    result.input_attribute = attribute
    result.input_paths = [artifact.path for artifact in selected if artifact.path]
    if selected:
        primary = selected[-1]
        result.artifact_path = primary.path
        primary.results.append(result)
    return result


async def _judge_one(
    check: Check,
    case: TestCase,
    context: ArtifactContext,
    semaphore: asyncio.Semaphore,
) -> CheckResult:
    selected = context.select(check.artifacts or ["final"])
    attribute = "tool_calls" if check.input == "tool_call" else check.input
    scenario = case.scenario or "presentation"
    try:
        user_instruction = case.user_phrase if check.use_user_phrase else None
        if attribute not in {"text", "raw_structure", "rendered_photos", "tool_calls"}:
            raise ValueError(f"Неподдерживаемый LLM input: {attribute}")
        context.materialize(selected, attribute, check.processor)
        if attribute == "rendered_photos":
            photos = [photo for artifact in selected for photo in artifact.rendered_photos]
            result = await _judge_vlm(check, user_instruction, photos, semaphore, scenario)
        elif attribute == "tool_calls":
            chunks = [
                f"--- {artifact.path or artifact.name} ---\n"
                + json.dumps(artifact.tool_calls, ensure_ascii=False, default=str)
                for artifact in selected
            ]
            result = await _judge_text(
                check,
                user_instruction,
                selected,
                semaphore,
                text_override="\n\n".join(chunks),
                source_override=f"tool_calls: {sum(len(a.tool_calls) for a in selected)}",
                scenario=scenario,
            )
        elif attribute == "raw_structure":
            chunks = [
                f"--- {artifact.path or artifact.name} ---\n"
                + context.serialize_raw_structure(artifact, check.processor)
                for artifact in selected
            ]
            result = await _judge_text(
                check,
                user_instruction,
                selected,
                semaphore,
                text_override="\n\n".join(chunks),
                source_override=f"raw_structure: {len(chunks)} артефактов",
                scenario=scenario,
            )
        else:
            result = await _judge_text(check, user_instruction, selected, semaphore, scenario=scenario)
        return _attach_result(check, result, selected, attribute)
    except Exception as error:
        result = _judger_error_result(check, f"ошибка судьи: {error}")
        return _attach_result(check, result, selected, attribute)


async def run_judge(case: TestCase, context: ArtifactContext) -> List[CheckResult]:
    checks = [check for check in case.check_list if check.kind == "llm"]
    if not checks:
        return []
    semaphore = asyncio.Semaphore(JUDGE_MAX_CONCURRENCY)
    return await asyncio.gather(
        *(_judge_one(check, case, context, semaphore) for check in checks)
    )