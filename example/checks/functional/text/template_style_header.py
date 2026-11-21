"""Проверка M1: STYLE.md содержит распознаваемый заголовок с именем шаблона."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from example.checks._helpers import ok, unavailable
from example.models import Artifact, Check, CheckResult

from ._template_style import declared_template_name


def run(
    check: Check,
    artifacts: List[Artifact],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    styles = [
        artifact
        for artifact in artifacts
        if (artifact.artifact_type or Path(artifact.path or "").suffix.lower()) == ".md"
    ]
    if not styles:
        return unavailable(check, "Нужен артефакт STYLE.md", artifacts, user_phrase)

    declared = declared_template_name(styles[-1].text or "")
    if declared is None:
        return unavailable(check, "В STYLE.md нет распознаваемого заголовка с именем шаблона", artifacts, user_phrase)
    return ok(check, f"STYLE.md объявляет шаблон {declared!r}", artifacts, user_phrase)
