"""Функциональная проверка `slide_fullness`: разреженная вёрстка слайдов.

Ловит слайды, где контент занимает малую часть холста в любом направлении:
пустое дно, пустые бока (например, контент прижат влево) или пустой верх.
Считается по page-N.png: охват контента по высоте и ширине — строки/колонки
с >= 3% нефоновых пикселей. FAIL для каждого слайда, где хотя бы одна ось
охвачена меньше 65%. Не оцениваются титульник и слайды-переходы (манифесты):
их разреженность — приём, картинки и крупная типографика на них не делают
слайд «наполненным» и учитываются только на слайдах с основным контентом.

Фон оценивается локально (медианный фильтр): цвет точки сравнивается с её
окрестностью, а не с углами кадра. Градиентная заливка фона при этом не
считается контентом — иначе «таблица сверху + градиентное пустое дно»
проходит проверку как заполненный слайд.

Единый интерфейс проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter

from ...models import Check, CheckResult
from ._helpers import is_transition, ok, slide_text_stats, unavailable

# Пиксель «фоновый», если RGB-расстояние до локального фона меньше порога.
_BG_DIST = 30.0
# Строка/колонка «контентная», если нефоновых пикселей в ней >= этой доли.
_ROW_CONTENT_FRAC = 0.03
# Охват контента по каждой оси (высота/ширина) ниже этого — разреженный слайд.
_AXIS_MIN = 0.65
# Окрестность оценки локального фона (медианный фильтр, нечётный размер).
_BG_MEDIAN_SIZE = 15
# Крупная сторона уменьшенной копии для анализа.
_ANALYSIS_MAX_SIDE = 360


def _content_extents(photo: str) -> Tuple[float, float]:
    """Охват контента слайда: (доля высоты, доля ширины) bbox контента.

    Локальный фон — медианный фильтр изображения: он «вытирает» локальный
    контент (текст, края карточек), оставляя плавную подложку. Плавный
    градиент фона совпадает со своей медианой и фоном считается, а буквы
    и границы элементов — нет.
    """
    im = Image.open(photo).convert("RGB")
    im.thumbnail((_ANALYSIS_MAX_SIDE, _ANALYSIS_MAX_SIDE))
    a = np.asarray(im, dtype=np.float32)
    h, w, _ = a.shape
    local_bg = np.asarray(
        im.filter(ImageFilter.MedianFilter(size=_BG_MEDIAN_SIZE)),
        dtype=np.float32,
    )
    is_bg = np.linalg.norm(a - local_bg, axis=2) < _BG_DIST
    rows = np.where((~is_bg).mean(axis=1) >= _ROW_CONTENT_FRAC)[0]
    cols = np.where((~is_bg).mean(axis=0) >= _ROW_CONTENT_FRAC)[0]
    if len(rows) == 0 or len(cols) == 0:
        return 0.0, 0.0
    rowf = (rows.max() - rows.min() + 1) / h
    colf = (cols.max() - cols.min() + 1) / w
    return rowf, colf


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """FAIL для каждого контентного слайда с охватом холста < _AXIS_MIN по оси."""
    if not photos:
        return unavailable(check, "Нет отрендеренных страниц для анализа", artifacts, photos, user_phrase)
    stats = slide_text_stats(next((a for a in artifacts if Path(a).suffix.lower() == ".pptx"), ""))
    flagged: List[str] = []
    try:
        for p in photos:
            m = re.search(r"page-(\d+)\.png$", p)
            slide_no = int(m.group(1)) if m else 0
            if slide_no == 1:
                continue  # титульник
            st = stats.get(slide_no)
            if st is not None and is_transition(st):
                continue  # слайд-переход/манифест
            rowf, colf = _content_extents(p)
            if rowf < _AXIS_MIN or colf < _AXIS_MIN:
                flagged.append(f"{slide_no} (высота {rowf:.0%}, ширина {colf:.0%})")
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Ошибка анализа рендеров: {e}", artifacts, photos, user_phrase)
    if not flagged:
        return ok(check, "Слайды заполнены контентом достаточно плотно на всех контентных слайдах",
                  artifacts, photos, user_phrase)
    return unavailable(
        check,
        f"Разреженная вёрстка: контент занимает малую часть слайда "
        f"(менее {_AXIS_MIN:.0%} высоты или ширины) на {len(flagged)} слайдах: {', '.join(flagged)}",
        artifacts, photos, user_phrase,
    )