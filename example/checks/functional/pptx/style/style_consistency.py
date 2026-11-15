"""Функциональная проверка `style_consistency`: единство стиля контентных слайдов.

Ловит слайд, выпавший из повторяющейся системы цветных контейнеров колоды:
на нём нет ни цветов системы, ни картинок/декора — только текст на фоне.

Правила:
  * контентный слайд: >=2 текстонесущих шейпа; титул не участвует;
  * словарь системы: цвета заливки автошейпов, встречающиеся минимум на 2
    контентных слайдах;
  * система есть, если >=50% контентных слайдов её используют; нет — PASS;
  * аутлаер: контентный слайд без цветов словаря и без картинок/групп.

Единый интерфейс проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from pptx import Presentation
from pptx.enum.dml import MSO_FILL_TYPE
from pptx.enum.shapes import MSO_SHAPE_TYPE

from example.models import Check, CheckResult
from example.checks.functional.pptx._helpers import ok, unavailable

# Заливка входит в словарь системы, если встречается минимум на стольких
# контентных слайдах.
_COMMON_MIN_SLIDES = 2
# Система есть, если словарь используют хотя бы такая доля контентных слайдов.
_SYSTEM_MIN_FRAC = 0.5
# Слайд контентный, если текстонесущих шейпов хотя бы столько.
_MIN_TEXT_SHAPES = 2


def _slide_features(pptx_path: str) -> Dict[int, dict]:
    """Слайд (1-based) -> {fills, text_shapes, has_visuals}."""
    prs = Presentation(pptx_path)
    out: Dict[int, dict] = {}
    for i, slide in enumerate(prs.slides, start=1):
        fills: Set[Tuple[int, int, int]] = set()
        text_shapes = 0
        has_visuals = False
        for shape in slide.shapes:
            try:
                st = shape.shape_type
                if st in (MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.GROUP,
                          MSO_SHAPE_TYPE.TABLE, MSO_SHAPE_TYPE.CHART, MSO_SHAPE_TYPE.MEDIA):
                    has_visuals = True
                if shape.has_text_frame and shape.text_frame.text.strip():
                    text_shapes += 1
                if st == MSO_SHAPE_TYPE.AUTO_SHAPE and shape.fill.type == MSO_FILL_TYPE.SOLID:
                    rgb = shape.fill.fore_color.rgb
                    if rgb is not None:
                        fills.add((rgb[0], rgb[1], rgb[2]))
            except (AttributeError, NotImplementedError, ValueError):
                continue
        out[i] = {"fills": fills, "text_shapes": text_shapes, "has_visuals": has_visuals}
    return out


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """FAIL, если голый контентный слайд выпал из системы контейнеров колоды."""
    pptx = next((a for a in artifacts if Path(a).suffix.lower() == ".pptx"), None)
    if not pptx:
        return unavailable(check, "Нет .pptx-артефакта для проверки", artifacts, photos, user_phrase)
    try:
        feats = _slide_features(pptx)
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать .pptx: {e}", artifacts, photos, user_phrase)

    # Контентные слайды без титульника (первый слайд).
    content = {i: f for i, f in feats.items() if i > 1 and f["text_shapes"] >= _MIN_TEXT_SHAPES}
    n = len(content)
    if n < 3:
        return ok(check, "Контентных слайдов слишком мало для оценки единства стиля", artifacts, photos, user_phrase)
    freq: Counter = Counter()
    for f in content.values():
        for fill in f["fills"]:
            freq[fill] += 1
    common = {fill for fill, c in freq.items() if c >= _COMMON_MIN_SLIDES}
    using = sum(1 for f in content.values() if not f["fills"].isdisjoint(common))
    if not common or using / n < _SYSTEM_MIN_FRAC:
        return ok(check, "Повторяющейся системы цветных плашек нет — нечего нарушать", artifacts, photos, user_phrase)

    outliers = sorted(
        i for i, f in content.items()
        if f["fills"].isdisjoint(common) and not f["has_visuals"]
    )
    if outliers:
        return unavailable(
            check,
            f"Слайды выбиваются из общего стиля колоды: на них нет ни фирменных "
            f"плашек/карточек, ни иллюстраций — только текст на голом фоне: {', '.join(map(str, outliers))}",
            artifacts, photos, user_phrase,
        )
    return ok(check, "Все контентные слайды выдержаны в общем стиле колоды", artifacts, photos, user_phrase)