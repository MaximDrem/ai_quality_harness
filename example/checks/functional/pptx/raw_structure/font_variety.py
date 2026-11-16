"""Функциональная проверка `font_variety`: шрифтовой хаос в колоде.

Считает число разных гарнитур по python-pptx. Названия нормализуются
(строчные, без начертаний), тематические плейсхолдеры (+mj-lt и т.п.) и
пустые имена игнорируются. FAIL, если гарнитур больше двух.

Единый интерфейс проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Set

from pptx import Presentation

from example.models import Artifact, Check, CheckResult
from example.checks.functional.pptx._pptx_helpers import ok, unavailable

_MAX_FAMILIES = 2

_WEIGHT = re.compile(
    r"\b(light|medium|semibold|semi\s*bold|bold|regular|italic|black|thin|"
    r"extrabold|extra\s*bold|condensed)\b",
    re.IGNORECASE,
)


def _normalize(name: str) -> str:
    """Гарнитура без начертания, строчными: «SB Sans Display Light» -> «sb sans display»."""
    n = _WEIGHT.sub(" ", name)
    return re.sub(r"\s+", " ", n).strip().lower()


def run(
    check: Check,
    artifacts: List[Artifact],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """FAIL, если в колоде больше двух разных гарнитур."""
    pptx = next((a for a in artifacts if (a.artifact_type or Path(a.path or "").suffix.lower()) == ".pptx"), None)
    if not pptx:
        return unavailable(check, "Нет .pptx-артефакта для проверки", artifacts, photos, user_phrase)
    try:
        prs = pptx.raw_structure
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать .pptx: {e}", artifacts, photos, user_phrase)

    families: Set[str] = set()
    try:
        for slide in prs.slides:
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        name = (run.font.name or "").strip()
                        if not name or name.startswith("+"):
                            continue  # пустое имя или тематический плейсхолдер
                        families.add(_normalize(name))
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Ошибка анализа слайдов: {e}", artifacts, photos, user_phrase)

    if len(families) > _MAX_FAMILIES:
        return unavailable(
            check,
            f"Шрифтовой разнобой: в колоде больше {_MAX_FAMILIES} разных шрифтов: "
            f"{', '.join(sorted(families))}",
            artifacts, photos, user_phrase,
        )
    shown = ", ".join(sorted(families)) if families else "—"
    return ok(check, f"Разных шрифтов в колоде: {len(families)} ({shown})", artifacts, photos, user_phrase)