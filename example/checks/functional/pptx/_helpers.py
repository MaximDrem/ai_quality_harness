"""Построители CheckResult для функциональных проверок.

Общие хелперы скриптов `checks/functional/<имя>.py`: ok/unavailable с единым
заполнением полей, плюс утилиты для .pptx — связка слайда с отрендеренной
страницей, перевод координат EMU в пиксели, цветовая математика (WCAG) и
детектор слайдов-переходов (манифестов).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pptx import Presentation

from example.models import Check, CheckResult
from example.checks.functional._helpers import ok, source_str, unavailable

# Слайд-переход: текста меньше этого числа символов...
_TRANSITION_MAX_CHARS = 120
# ...и текстонесущих шейпов не больше этого числа (номер раздела, заголовок, подзаголовок).
_TRANSITION_MAX_SHAPES = 3


def source_str(artifacts: List[str], photos: List[str], user_phrase: Optional[str]) -> str:
    """Строка «что отдано проверке» для поля source результата."""
    src = f"артефактов: {len(artifacts)}, фото: {len(photos)}"
    src += f", user_prompt: {'да' if user_phrase else 'нет'}"
    return src


def slide_text_stats(pptx_path: str) -> Dict[int, Dict[str, int]]:
    """Слайд (1-based) -> {'shapes': текстонесущих шейпов, 'chars': символов}.

    Таблицы считаются текстонесущими шейпами: текст ячеек входит в chars.
    Иначе слайд-таблица (заголовок + таблица, у graphic frame нет
    text_frame) выглядит «пустым» и ошибочно исключается как переход.

    Пустой dict, если .pptx нет или не разобрался: тогда проверки
    наполненности работают без исключения переходов.
    """
    try:
        prs = Presentation(pptx_path)
    except Exception:  # noqa: BLE001 — нет карты шейпов: работаем без исключений
        return {}
    out: Dict[int, Dict[str, int]] = {}
    for i, slide in enumerate(prs.slides, start=1):
        shapes = chars = 0
        for sh in slide.shapes:
            try:
                if getattr(sh, "has_table", False):
                    cells = (c.text for row in sh.table.rows for c in row.cells)
                    text = " ".join(t for t in cells if t.strip()).strip()
                    if text:
                        shapes += 1
                        chars += len(text)
                    continue
                if sh.has_text_frame and sh.text_frame.text.strip():
                    shapes += 1
                    chars += len(sh.text_frame.text.strip())
            except (AttributeError, NotImplementedError, ValueError):
                continue
        out[i] = {"shapes": shapes, "chars": chars}
    return out


def is_transition(stats: Dict[str, int]) -> bool:
    """Слайд-переход/манифест: текста < 120 символов и не больше 3 шейпов.

    Разреженность таких слайдов (раздел раздела, цитата, лозунг) — приём,
    поэтому проверки наполненности их не оценивают: картинки и крупная
    типографика не должны «спасать» их и учитываются только на слайдах
    с основным контентом. Слайд с малым числом шейпов, но плотным текстом
    (весь контент в одном фрейме) — контентный, не переход.
    """
    return (stats["chars"] < _TRANSITION_MAX_CHARS
            and stats["shapes"] <= _TRANSITION_MAX_SHAPES)


def ok(
    check: Check,
    reason: str,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str],
) -> CheckResult:
    """Результат «проверка пройдена»."""
    return CheckResult(
        check=check,
        success=True,
        reason=reason,
        mode=check.mode,
        source=source_str(artifacts, photos, user_phrase),
    )


def unavailable(
    check: Check,
    reason: str,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str],
) -> CheckResult:
    """Результат «проверка не пройдена / невозможна»."""
    return CheckResult(
        check=check,
        success=False,
        reason=reason,
        mode=check.mode,
        source=source_str(artifacts, photos, user_phrase),
    )


def photo_for_slide(photos: List[str], slide_no: int) -> Optional[str]:
    """Найти отрендеренную страницу `page-<slide_no>.png` среди фото."""
    for p in photos:
        m = re.search(r"page-(\d+)\.png$", p)
        if m and int(m.group(1)) == slide_no:
            return p
    return None


def shape_bbox_px(shape, slide_w_emu: int, slide_h_emu: int, img_w: int, img_h: int) -> Optional[Tuple[int, int, int, int]]:
    """BBox шейпа (EMU) -> пиксели рендера; None, если геометрии нет."""
    try:
        l, t, w, h = shape.left, shape.top, shape.width, shape.height
    except AttributeError:
        return None
    if l is None or t is None or w is None or h is None or w <= 0 or h <= 0:
        return None
    x0 = int(l / slide_w_emu * img_w)
    y0 = int(t / slide_h_emu * img_h)
    x1 = int((l + w) / slide_w_emu * img_w)
    y1 = int((t + h) / slide_h_emu * img_h)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img_w, x1), min(img_h, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return (x0, y0, x1, y1)


def rel_luminance(rgb: Tuple[int, int, int]) -> float:
    """Относительная яркость WCAG (0..1)."""
    def _lin(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
    """WCAG-контраст двух цветов (1..21)."""
    la, lb = rel_luminance(a), rel_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def dominant_colors(img, n: int = 6) -> List[Tuple[Tuple[int, int, int], float]]:
    """Палитра из n доминантных цветов кропа: [(rgb, доля)], по убыванию доли."""
    q = img.convert("RGB").quantize(colors=n, method=2)
    pal = q.getpalette()[: n * 3]
    counts = sorted(q.getcolors(), reverse=True)
    total = sum(c for c, _ in counts) or 1
    out = []
    for cnt, idx in counts:
        off = idx * 3
        out.append(((pal[off], pal[off + 1], pal[off + 2]), cnt / total))
    return out


def shape_bbox_emu(shape) -> Optional[Tuple[int, int, int, int]]:
    """BBox шейпа в EMU (left, top, right, bottom); None, если геометрии нет."""
    try:
        l, t, w, h = shape.left, shape.top, shape.width, shape.height
    except AttributeError:
        return None
    if l is None or t is None or w is None or h is None or w <= 0 or h <= 0:
        return None
    return (int(l), int(t), int(l + w), int(t + h))


def overlap_area_frac(inner, outer) -> float:
    """Доля площади `inner` (bbox EMU), перекрытая `outer` (bbox EMU)."""
    x0 = max(inner[0], outer[0])
    y0 = max(inner[1], outer[1])
    x1 = min(inner[2], outer[2])
    y1 = min(inner[3], outer[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return ((x1 - x0) * (y1 - y0)) / area if area > 0 else 0.0