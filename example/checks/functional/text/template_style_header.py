"""Проверка M1: STYLE.md ссылается на приложенный шаблон."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from example.checks._helpers import ok, unavailable
from example.models import Artifact, Check, CheckResult

from ._template_style import declared_template_name


def _normalize(value: str) -> str:
    return value.strip().strip("\"'").casefold()


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
    templates = [
        artifact
        for artifact in artifacts
        if (artifact.artifact_type or Path(artifact.path or "").suffix.lower()) == ".pptx"
    ]
    if not styles or not templates:
        return unavailable(check, "Нужны артефакты STYLE.md и шаблон .pptx", artifacts, user_phrase)

    style = styles[-1]
    template = templates[-1]
    declared = declared_template_name(style.text or "")
    if declared is None:
        return unavailable(check, "В STYLE.md нет распознаваемого заголовка с именем шаблона", artifacts, user_phrase)
    expected = Path(template.path or "").name
    if _normalize(declared) != _normalize(expected):
        return unavailable(
            check,
            f"STYLE.md объявляет шаблон {declared!r}, ожидается {expected!r}",
            artifacts,
            user_phrase,
        )
    return ok(check, f"STYLE.md ссылается на приложенный шаблон {expected!r}", artifacts, user_phrase)
