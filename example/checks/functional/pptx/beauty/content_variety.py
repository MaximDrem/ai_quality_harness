"""Функциональная проверка `content_variety`: структурное разнообразие слайдов.

Признак структурной подачи: на слайде смешаны разные типы элементов, а не
однотипные карточки. Считается по python-pptx: число разных типов шейпов.
PASS, если >= 20% контентных слайдов имеют 3 и более разных типа.

Единый интерфейс проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pptx import Presentation

from example.models import Check, CheckResult
from example.checks.functional.pptx._helpers import ok, unavailable

# Слайд «структурно богатый», если на нём столько и более разных типов шейпов.
_MIN_TYPES = 3
# Доля таких слайдов среди контентных, ниже которой — FAIL.
_MIN_RICH_FRAC = 0.2


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """FAIL, если контентные слайды однотипны (нет микса шейпов)."""
    pptx = next((a for a in artifacts if Path(a).suffix.lower() == ".pptx"), None)
    if not pptx:
        return unavailable(check, "Нет .pptx-артефакта для проверки", artifacts, photos, user_phrase)
    try:
        prs = Presentation(pptx)
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать .pptx: {e}", artifacts, photos, user_phrase)

    rich: List[int] = []
    total = 0
    try:
        for i, slide in enumerate(prs.slides, start=1):
            if i == 1:
                continue  # титульник
            total += 1
            types = set()
            for shape in slide.shapes:
                try:
                    types.add(shape.shape_type)
                except (AttributeError, NotImplementedError, ValueError):
                    continue
            if len(types) >= _MIN_TYPES:
                rich.append(i)
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Ошибка анализа слайдов: {e}", artifacts, photos, user_phrase)

    if not total:
        return unavailable(check, "Нет контентных слайдов для анализа", artifacts, photos, user_phrase)
    frac = len(rich) / total
    if frac < _MIN_RICH_FRAC:
        return unavailable(
            check,
            f"Подача однотипна: слайдов, где смешаны разные типы элементов "
            f"(карточки + таблицы + картинки и т.п.), {len(rich)} из {total} "
            f"({frac:.0%}, порог {_MIN_RICH_FRAC:.0%})",
            artifacts, photos, user_phrase,
        )
    return ok(
        check,
        f"Подача разнообразна: слайдов со смесью разных типов элементов {len(rich)} из {total} "
        f"({frac:.0%}): {', '.join(str(s) for s in rich)}",
        artifacts, photos, user_phrase,
    )