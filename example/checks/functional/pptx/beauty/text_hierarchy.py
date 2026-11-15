"""Функциональная проверка `text_hierarchy`: типографическая иерархия колоды.

Признак проработанной типографики: несколько уровней текста (титул, заголовок,
тело, подпись) — значит, число разных кеглей в колоде. Считается кодом по
python-pptx (размеры ранов, округлённые до целых пунктов). PASS, если разных
кеглей >= 15.

Единый интерфейс функциональной проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set

from pptx import Presentation

from example.models import Check, CheckResult
from example.checks.functional.pptx._helpers import ok, unavailable

# Число разных кеглей в колоде, ниже которого — FAIL.
_MIN_SIZES = 15


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """FAIL, если в колоде мало уровней кегля (плоская типографика)."""
    pptx = next((a for a in artifacts if Path(a).suffix.lower() == ".pptx"), None)
    if not pptx:
        return unavailable(check, "Нет .pptx-артефакта для проверки", artifacts, photos, user_phrase)
    try:
        prs = Presentation(pptx)
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать .pptx: {e}", artifacts, photos, user_phrase)

    sizes: Set[int] = set()
    try:
        for slide in prs.slides:
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        if run.font.size is not None:
                            sizes.add(round(run.font.size.pt))
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Ошибка анализа слайдов: {e}", artifacts, photos, user_phrase)

    if len(sizes) < _MIN_SIZES:
        return unavailable(
            check,
            f"Типографика плоская: в колоде всего {len(sizes)} разных размеров шрифта "
            f"(порог {_MIN_SIZES}), нет выраженной иерархии «заголовок/текст/подпись»: "
            f"{sorted(sizes)}",
            artifacts, photos, user_phrase,
        )
    return ok(
        check,
        f"Иерархия типографики проработана: разных размеров шрифта {len(sizes)}",
        artifacts, photos, user_phrase,
    )