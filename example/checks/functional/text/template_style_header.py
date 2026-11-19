"""Проверка M1: STYLE.md содержит корректный заголовок с именем шаблона."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from example.checks._helpers import ok, unavailable
from example.models import Artifact, Check, CheckResult


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
        return unavailable(check, "Нет промежуточного STYLE.md", artifacts, user_phrase)
    declared = (styles[-1].text or "").strip()
    if not declared.startswith("# STYLE.md"):
        return unavailable(check, "STYLE.md не начинается с заголовка # STYLE.md", artifacts, user_phrase)
    from ._template_style import declared_template_name

    if declared_template_name(declared) is None:
        return unavailable(
            check,
            "Заголовок STYLE.md должен иметь вид «# STYLE.md — <шаблон> — N slides, canvas WxH»",
            artifacts,
            user_phrase,
        )
    return ok(check, "Заголовок STYLE.md распознан", artifacts, user_phrase)
