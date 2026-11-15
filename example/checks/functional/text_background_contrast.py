"""Функциональная проверка `text_background_contrast`: текст сливается с фоном.

Для каждого текстового шейпа берётся пара цветов: фон — заливка шейпа или
доминантный цвет кропа рендера, текст — цвет рунов или самый далёкий от фона
цвет кропа. Контраст считается по WCAG. FAIL, если контраст ниже порога
хотя у одного текста на одном слайде.

Единый интерфейс проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image
from pptx import Presentation
from pptx.enum.dml import MSO_FILL_TYPE

from ...models import Check, CheckResult
from ._helpers import (
    contrast_ratio,
    dominant_colors,
    ok,
    photo_for_slide,
    shape_bbox_px,
    unavailable,
)

# Контраст ниже этого — «сливается» (WCAG AA = 4.5, но ловим грубое слияние).
# FAIL при хотя бы одном таком тексте.
_MIN_RATIO = 1.8
# Минимальная длина текста, чтобы считать его содержанием.
_MIN_TEXT_LEN = 4


def _solid_fill_rgb(shape) -> Optional[Tuple[int, int, int]]:
    """RGB сплошной заливки шейпа, если объявлена явно."""
    try:
        if shape.fill.type != MSO_FILL_TYPE.SOLID:
            return None
        rgb = shape.fill.fore_color.rgb
        return (rgb[0], rgb[1], rgb[2]) if rgb is not None else None
    except (AttributeError, NotImplementedError, ValueError):
        return None


def _declared_text_rgb(shape) -> Optional[Tuple[int, int, int]]:
    """Самый частый объявленный цвет рунов текста шейпа, если есть."""
    counts: Counter = Counter()
    try:
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                rgb = run.font.color.rgb
                if rgb is not None:
                    counts[(rgb[0], rgb[1], rgb[2])] += max(1, len(run.text.strip()))
    except (AttributeError, NotImplementedError, ValueError):
        return None
    return counts.most_common(1)[0][0] if counts else None


def _slide_low_contrast_shapes(slide, photo: Optional[str], slide_wh, img_size) -> List[Tuple[str, float]]:
    """Тексты шейпов слайда с контрастом ниже порога: [(текст, контраст)]."""
    img = Image.open(photo) if photo else None
    bad: List[Tuple[str, float]] = []
    for shape in slide.shapes:
        try:
            if not shape.has_text_frame:
                continue
            text = shape.text_frame.text.strip()
            if len(text) < _MIN_TEXT_LEN:
                continue
            # Фон берём из рендера (доминантный цвет кропа): заливка не
            # учитывает прозрачность и подложку-соседа. Заливка — запасной
            # вариант.
            bg_rgb = None
            text_rgb = _declared_text_rgb(shape)
            bbox = None
            if img is not None:
                bbox = shape_bbox_px(shape, *slide_wh, *img_size)
                if bbox is not None:
                    x0, y0, x1, y1 = bbox
                    # Внутренний отступ, чтобы не цеплять рамку/края.
                    mx, my = max(2, (x1 - x0) // 12), max(2, (y1 - y0) // 12)
                    crop = img.crop((x0 + mx, y0 + my, x1 - mx, y1 - my))
                    pal = dominant_colors(crop, n=6)
                    if pal:
                        bg_rgb = pal[0][0]
                        if text_rgb is None:
                            cand = [(contrast_ratio(bg_rgb, c), c) for c, share in pal[1:] if share >= 0.02]
                            if cand:
                                text_rgb = max(cand)[1]
            if bg_rgb is None:
                bg_rgb = _solid_fill_rgb(shape)
            if bg_rgb is None or text_rgb is None:
                continue
            if contrast_ratio(bg_rgb, text_rgb) < _MIN_RATIO:
                bad.append((text[:40], contrast_ratio(bg_rgb, text_rgb)))
        except (AttributeError, NotImplementedError, ValueError):
            continue
    return bad


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """FAIL, если контраст текста к фону ниже порога хотя у одного текста."""
    pptx = next((a for a in artifacts if Path(a).suffix.lower() == ".pptx"), None)
    if not pptx:
        return unavailable(check, "Нет .pptx-артефакта для проверки", artifacts, photos, user_phrase)

    try:
        prs = Presentation(pptx)
        slide_wh = (prs.slide_width, prs.slide_height)
        flagged: List[str] = []
        for i, slide in enumerate(prs.slides, start=1):
            photo = photo_for_slide(photos, i)
            img_size = Image.open(photo).size if photo else (0, 0)
            bad = _slide_low_contrast_shapes(slide, photo, slide_wh, img_size)
            if bad:
                worst = min(bad, key=lambda b: b[1])
                flagged.append(f"{i} ({worst[0]}…, {worst[1]:.1f})")
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Ошибка анализа контраста: {e}", artifacts, photos, user_phrase)
    if flagged:
        return unavailable(
            check,
            f"Текст почти неотличим от фона (сливается с ним по цвету, "
            f"контраст ниже {_MIN_RATIO}) "
            f"на {len(flagged)} слайдах: {', '.join(flagged)}",
            artifacts, photos, user_phrase,
        )
    return ok(check, "Цвет текста достаточно контрастен к фону везде",
              artifacts, photos, user_phrase)