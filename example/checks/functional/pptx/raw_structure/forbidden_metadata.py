"""Проверка M5: запрещённые следы отсутствуют в метаданных презентации."""

from __future__ import annotations

from typing import List, Optional

from example.checks._helpers import ok, unavailable
from example.checks.functional.pptx._template_helpers import (
    METADATA_PARTS,
    final_presentation,
    find_forbidden,
    forbidden_patterns,
)
from example.models import Artifact, Check, CheckResult

_PRESENTATION_PARTS = {"ppt/presentation.xml", "ppt/viewProps.xml", "ppt/presProps.xml"}


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
        matches = find_forbidden(
            presentation.path,
            check,
            lambda name: name.endswith(".xml")
            and (name.startswith(METADATA_PARTS) or name in _PRESENTATION_PARTS),
        )
    except Exception as error:  # noqa: BLE001
        return unavailable(check, f"Не удалось прочитать PPTX: {error}", artifacts, user_phrase)
    if matches:
        return unavailable(check, "Следы шаблона в метаданных: " + "; ".join(matches[:10]), artifacts, user_phrase)
    return ok(check, "В метаданных запрещённые следы шаблона не найдены", artifacts, user_phrase)
