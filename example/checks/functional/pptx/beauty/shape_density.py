"""Функциональная проверка `shape_density`: плотность элементов на слайде.

Признак проработанности слайда: медианное число шейпов (карточки, подписи,
метки, таблицы, декор) на контентных слайдах. Считается кодом по python-pptx.
PASS, если медиана >= 15 шейпов на слайд.

Единый интерфейс функциональной проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pptx import Presentation

from example.models import Check, CheckResult
from example.checks.functional.pptx._helpers import ok, unavailable

# Медианное число шейпов на контентный слайд, ниже которого — FAIL.
_MIN_SHAPES_MEDIAN = 15


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """FAIL, если слайды слабо наполнены элементами (медиана ниже порога)."""
    pptx = next((a for a in artifacts if Path(a).suffix.lower() == ".pptx"), None)
    if not pptx:
        return unavailable(check, "Нет .pptx-артефакта для проверки", artifacts, photos, user_phrase)
    try:
        prs = Presentation(pptx)
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать .pptx: {e}", artifacts, photos, user_phrase)

    counts: List[int] = []
    try:
        for i, slide in enumerate(prs.slides, start=1):
            if i == 1:
                continue  # титульник
            counts.append(len(list(slide.shapes)))
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Ошибка анализа слайдов: {e}", artifacts, photos, user_phrase)

    if not counts:
        return unavailable(check, "Нет контентных слайдов для анализа", artifacts, photos, user_phrase)
    counts.sort()
    median = counts[len(counts) // 2]
    if median < _MIN_SHAPES_MEDIAN:
        return unavailable(
            check,
            f"Слайды бедно наполнены: на типичном слайде {median} элементов "
            f"(карточки, подписи, плашки, декор; порог {_MIN_SHAPES_MEDIAN}). "
            f"По слайдам: {counts}",
            artifacts, photos, user_phrase,
        )
    return ok(
        check,
        f"Слайды наполнены: на типичном слайде {median} элементов. По слайдам: {counts}",
        artifacts, photos, user_phrase,
    )