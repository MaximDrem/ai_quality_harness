"""Заглушка будущей орфографической проверки Artifact.text."""

from __future__ import annotations

from example.models import Check, CheckResult


def run(check: Check, artifacts, user_phrase=None, context=None) -> CheckResult:
    return CheckResult(
        check=check,
        success=True,
        reason="Проверка-заглушка: детектор опечаток пока не реализован",
        mode=check.mode,
        source=f"артефактов: {len(artifacts)}",
    )
