"""Функциональная проверка `repeated_words`: удвоенные слова в тексте колоды.

Считаются только полнословные повторы внутри одного абзаца («слово слово»,
регистр не важен); числа, однобуквенные слова и повторы через знаки
препинания — не дефект.

Единый интерфейс проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from pptx import Presentation

from ...models import Check, CheckResult
from ._helpers import ok, unavailable

# Удвоение — два одинаковых слова подряд, между ними только пробельные
# символы. Цифры и знаки препинания между словами разрывают пару: иначе
# «LTV 58 000 ₽ · LTV/CAC» ложно считается удвоением «LTV LTV».
_DOUBLE = re.compile(r"\b([A-Za-zА-Яа-яЁё]{2,})(?:[ \t\n\u00A0]+)\1\b", re.IGNORECASE)


def _find_doubles(text: str) -> List[str]:
    """Список удвоенных слов абзаца (в исходном написании первого вхождения)."""
    return [m.group(1) for m in _DOUBLE.finditer(text)]


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """PASS, если в тексте колоды нет удвоенных слов."""
    pptx = next((a for a in artifacts if Path(a).suffix.lower() == ".pptx"), None)
    if not pptx:
        return unavailable(check, "Нет .pptx-артефакта для проверки", artifacts, photos, user_phrase)
    try:
        prs = Presentation(pptx)
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать .pptx: {e}", artifacts, photos, user_phrase)

    hits: List[str] = []
    for i, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                for w in _find_doubles(para.text):
                    hits.append(f"слайд {i} («{w} {w}»)")
    if not hits:
        return ok(check, "Удвоенных слов нет", artifacts, photos, user_phrase)
    return unavailable(
        check,
        "Удвоенные слова (ошибка генерации): " + ", ".join(hits[:10]),
        artifacts, photos, user_phrase,
    )