"""Функциональная проверка `escaped_newlines`: буквальные «\n» в тексте колоды.

Ловит перенос строки, записанный как два буквальных символа («\n») вместо
разрыва: на слайдах появляются «Финальные\nзадачи». Сканируется весь текст
колоды (фреймы шейпов и ячейки таблиц), FAIL при хотя бы одном вхождении.

Единый интерфейс проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from ...models import Check, CheckResult
from ._helpers import ok, unavailable

# Буквальная последовательность «обратный слэш + n» (не разрыв строки).
_LITERAL_NL = re.compile(r"\\n")


def _shape_texts(slide) -> List[str]:
    """Весь текст слайда: текстовые фреймы шейпов + ячейки таблиц."""
    texts: List[str] = []
    for shape in slide.shapes:
        try:
            if shape.shape_type == MSO_SHAPE_TYPE.TABLE:
                for row in shape.table.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            texts.append(cell.text)
            elif shape.has_text_frame and shape.text_frame.text.strip():
                texts.append(shape.text_frame.text)
        except (AttributeError, NotImplementedError, ValueError):
            continue
    return texts


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """FAIL, если в тексте колоды есть буквальные «\n»."""
    pptx = next((a for a in artifacts if Path(a).suffix.lower() == ".pptx"), None)
    if not pptx:
        return unavailable(check, "Нет .pptx-артефакта для проверки", artifacts, photos, user_phrase)
    try:
        prs = Presentation(pptx)
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать .pptx: {e}", artifacts, photos, user_phrase)

    hits: List[str] = []
    for i, slide in enumerate(prs.slides, 1):
        for text in _shape_texts(slide):
            m = _LITERAL_NL.search(text)
            if m:
                start = max(0, m.start() - 12)
                fragment = text[start:m.end() + 8].replace("\n", " ")
                hits.append(f"слайд {i} («{fragment}…»)")
                break
    if not hits:
        return ok(check, "Буквальных «\\n» в тексте нет", artifacts, photos, user_phrase)
    return unavailable(
        check,
        "Буквальные «\\n» вместо переноса строки (ошибка генерации): " + ", ".join(hits[:10]),
        artifacts, photos, user_phrase,
    )