"""Оркестрация судейства: run_judge и посайдовые режимы.

Проверки text/vlm из чек-листа кейса идут параллельно, число
одновременных запросов к vLLM ограничено JUDGE_MAX_CONCURRENCY.
Проверки с `single_slide: true` судятся по одному слайду за запрос,
вердикты агрегируются: FAIL, если упал хотя бы один слайд.
"""
from __future__ import annotations

import asyncio
import re
from typing import List, Optional, Tuple

from ..config import JUDGE_TEXT_MAX_CHARS
from ..models import Check, CheckResult, TestCase
from .client import JUDGE_MAX_CONCURRENCY, judge_images, judge_text
from .extract import parse_artifacts_text


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
) -> CheckResult:
    """Судить vlm-проверку по одному слайду за запрос."""
    async def _one(idx: int, photo: str) -> Tuple[str, object]:
        async with semaphore:
            verdict = await judge_images(
                image_paths=[photo],
                user_instruction=user_instruction,
                check_instruction=check.text,
                first_slide=idx,
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
        return await _judge_by_slides(check, user_instruction, photos, semaphore)
    # Вся колода одним запросом — для проверок, оценивающих её целиком.
    async with semaphore:
        verdict = await judge_images(
            image_paths=photos,
            user_instruction=user_instruction,
            check_instruction=check.text,
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
    artifacts: List[str],
    semaphore: asyncio.Semaphore,
) -> CheckResult:
    """Текстовая проверка по распарсенному тексту артефактов."""
    text = parse_artifacts_text(artifacts)
    if not text.strip():
        return CheckResult(
            check=check,
            success=False,
            reason="Не удалось извлечь текст из артефакта для текстовой проверки",
            mode=check.mode,
            source="—",
        )
    if check.single_slide:
        # Каждый слайд судится отдельным запросом.
        return await _judge_text_by_slides(check, user_instruction, text, semaphore)
    async with semaphore:
        verdict = await judge_text(
            agent_text=text[:JUDGE_TEXT_MAX_CHARS],
            user_instruction=user_instruction,
            check_instruction=check.text,
        )
    return CheckResult(
        check=check,
        success=_is_pass(verdict),
        reason=verdict.reason,
        mode=check.mode,
        source=f"текст: {len(text)} симв.",
    )


async def _judge_one(
    check: Check,
    case: TestCase,
    artifacts: List[str],
    photos: List[str],
    semaphore: asyncio.Semaphore,
) -> CheckResult:
    """Выполнить одну проверку (text|vlm) через судью-LLM."""
    try:
        # user_phrase передаётся судье только если это разрешено полем
        # user_phrase проверки.
        user_instruction = case.user_phrase if check.use_user_phrase else None
        if check.kind == "vlm":
            return await _judge_vlm(check, user_instruction, photos, semaphore)
        return await _judge_text(check, user_instruction, artifacts, semaphore)
    except Exception as e:  # судья упал — не валим кейс
        return _judger_error_result(check, f"ошибка судьи: {e}")


async def run_judge(case: TestCase, artifacts: List[str], photos: List[str]) -> List[CheckResult]:
    """Прогнать text/vlm-проверки чек-листа кейса через судью-LLM.

    `functional`-проверки сюда не попадают — их считает код
    (`example.checks.functional`).
    """
    checks = [c for c in case.check_list if c.kind != "functional"]
    if not checks:
        return []

    semaphore = asyncio.Semaphore(JUDGE_MAX_CONCURRENCY)
    return await asyncio.gather(
        *(_judge_one(c, case, artifacts, photos, semaphore) for c in checks)
    )