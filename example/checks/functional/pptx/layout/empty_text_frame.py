"""Функциональная проверка `empty_text_frame`: пустой текстовый контейнер.

Ловит заметный автошейп со сплошной заливкой, но без текста и без
перекрывающего его текста/картинки. Подложка с текстом поверх («цветная
карточка + текстовый фрейм») — не дефект. Считается кодом по python-pptx.

Единый интерфейс проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pptx import Presentation
from pptx.enum.dml import MSO_FILL_TYPE
from pptx.enum.shapes import MSO_SHAPE_TYPE

from example.models import Check, CheckResult
from example.checks.functional.pptx._helpers import ok, overlap_area_frac, shape_bbox_emu, unavailable

# Минимальные доли ширины/высоты слайда, меньше — декор.
_MIN_W_FRAC = 0.12
_MIN_H_FRAC = 0.06
# Контейнер считается использованным, если текст/картинки суммарно
# накрывают хотя бы эту долю его площади. Порог низкий намеренно: метка
# или номер поверх чипа таймлайна занимает ~15% его площади, но контейнер
# при этом используется; реально пустой плейсхолдер не накрыт вовсе.
_COVER_FRAC = 0.08


def _empty_filled_shapes(pptx_path: str) -> List[int]:
    """Номера слайдов (1-based) с заметным залитым автошейпом без текста
    и без перекрывающего содержимого."""
    prs = Presentation(pptx_path)
    sw, sh = prs.slide_width, prs.slide_height
    flagged: List[int] = []
    for i, slide in enumerate(prs.slides, start=1):
        shapes = list(slide.shapes)
        # «Крышки»: текстовые шейпы с текстом и картинки.
        lids = []
        for s in shapes:
            try:
                has_text = s.has_text_frame and s.text_frame.text.strip()
                is_pic = s.shape_type == MSO_SHAPE_TYPE.PICTURE
                if has_text or is_pic:
                    bb = shape_bbox_emu(s)
                    if bb:
                        lids.append(bb)
            except (AttributeError, NotImplementedError, ValueError):
                continue
        for shape in shapes:
            try:
                if shape.shape_type != MSO_SHAPE_TYPE.AUTO_SHAPE:
                    continue
                if not shape.has_text_frame:
                    continue
                if shape.text_frame.text.strip():
                    continue
                if shape.fill.type != MSO_FILL_TYPE.SOLID:
                    continue
                w, h = shape.width, shape.height
                if w is None or h is None:
                    continue
                if not (w / sw >= _MIN_W_FRAC and h / sh >= _MIN_H_FRAC):
                    continue
                bb = shape_bbox_emu(shape)
                if bb is None:
                    continue
                covered = sum(overlap_area_frac(bb, lid) for lid in lids)
                if covered >= _COVER_FRAC:
                    continue  # подложка используется
                flagged.append(i)
                break
            except (AttributeError, NotImplementedError, ValueError):
                continue
    return flagged


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """FAIL, если в колоде есть заметный залитый контейнер без содержимого."""
    pptx = next((a for a in artifacts if Path(a).suffix.lower() == ".pptx"), None)
    if not pptx:
        return unavailable(check, "Нет .pptx-артефакта для проверки", artifacts, photos, user_phrase)
    try:
        flagged = _empty_filled_shapes(pptx)
    except Exception as e:  # noqa: BLE001 — битый pptx считаем невозможностью
        return unavailable(check, f"Не удалось разобрать .pptx: {e}", artifacts, photos, user_phrase)
    if flagged:
        slides = ", ".join(map(str, sorted(set(flagged))))
        return unavailable(
            check,
            f"Забытая пустая заглушка: цветная плашка без текста и другого содержимого "
            f"на слайдах: {slides}",
            artifacts, photos, user_phrase,
        )
    return ok(check, "Пустых цветных плашек-заглушек нет", artifacts, photos, user_phrase)