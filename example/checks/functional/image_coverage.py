"""Функциональная проверка `image_coverage`: наполненность слайдов изображениями.

Ловит крупные изображения (фоны, фото, иллюстрации), а не мелкие иконки.
Считается по python-pptx: доля площади слайда, занятая picture-шейпами.
PASS, если минимум на двух слайдах изображения занимают >= 15% площади.

Единый интерфейс проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from ...models import Check, CheckResult
from ._helpers import ok, unavailable

# Слайд считается «с изображением», если картинки занимают >= этой доли площади.
_PIC_AREA_MIN = 0.15
# FAIL, если слайдов с крупными изображениями меньше этого числа.
_PASS_SLIDES = 2


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """FAIL, если в колоде нет крупных изображений (мелкие иконки не в счёт)."""
    pptx = next((a for a in artifacts if Path(a).suffix.lower() == ".pptx"), None)
    if not pptx:
        return unavailable(check, "Нет .pptx-артефакта для проверки", artifacts, photos, user_phrase)
    try:
        prs = Presentation(pptx)
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать .pptx: {e}", artifacts, photos, user_phrase)

    big: List[str] = []
    try:
        for i, slide in enumerate(prs.slides, start=1):
            area = 0
            for shape in slide.shapes:
                try:
                    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                        area += shape.width * shape.height
                except (AttributeError, NotImplementedError, ValueError):
                    continue
            frac = area / (prs.slide_width * prs.slide_height)
            if frac >= _PIC_AREA_MIN:
                big.append(f"{i} ({frac:.0%})")
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Ошибка анализа слайдов: {e}", artifacts, photos, user_phrase)

    if len(big) >= _PASS_SLIDES:
        return ok(
            check,
            f"Крупные изображения (от {_PIC_AREA_MIN:.0%} площади слайда) есть на {len(big)} слайдах: {', '.join(big)}",
            artifacts, photos, user_phrase,
        )
    if not big:
        reason = (f"Картинок нет вовсе: ни одного слайда с крупным изображением "
                  f"(от {_PIC_AREA_MIN:.0%} площади слайда)")
    else:
        reason = (f"Картинок почти нет: крупные изображения (от {_PIC_AREA_MIN:.0%} площади слайда) "
                  f"есть только на {len(big)} слайдах, а нужно не меньше {_PASS_SLIDES}")
    return unavailable(check, reason, artifacts, photos, user_phrase)