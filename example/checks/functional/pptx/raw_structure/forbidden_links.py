"""Проверка M5: запрещённые ссылки отсутствуют в видимых relationship-частях."""

from __future__ import annotations

from typing import List, Optional

from example.checks._helpers import ok, unavailable
from example.checks.functional.pptx._template_helpers import (
    final_presentation,
    find_forbidden,
    forbidden_patterns,
)
from example.models import Artifact, Check, CheckResult


def _is_visible_rels(name: str) -> bool:
    return (
        name.endswith(".rels")
        and (
            name.startswith("ppt/slides/_rels/")
            or name.startswith("ppt/notesSlides/_rels/")
            or name.startswith("docProps/_rels/")
            or name == "ppt/_rels/presentation.xml.rels"
        )
    )


def run(
    check: Check,
    artifacts: List[Artifact],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    presentation = final_presentation(artifacts)
    if presentation is None or not presentation.path:
        return unavailable(check, "Нет финальной презентации .pptx", artifacts, user_phrase)
    if not forbidden_patterns(check):
        return unavailable(check, "Не заданы запрещённые terms/patterns", artifacts, user_phrase)
    try:
        matches = find_forbidden(presentation.path, check, _is_visible_rels)
    except Exception as error:  # noqa: BLE001
        return unavailable(check, f"Не удалось прочитать PPTX: {error}", artifacts, user_phrase)
    if matches:
        return unavailable(check, "Запрещённые ссылки: " + "; ".join(matches[:10]), artifacts, user_phrase)
    return ok(check, "В видимых ссылках запрещённые следы шаблона не найдены", artifacts, user_phrase)
