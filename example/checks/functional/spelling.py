"""Заглушка детектора опечаток: всегда PASS.

Детекция опечаток выполняется функциональной проверкой, а не судьёй-LLM.
"""
from __future__ import annotations

from typing import List, Optional

from ...models import Check, CheckResult


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    return CheckResult(
        check=check,
        success=True,
        reason="Проверка-заглушка: детектор опечаток пока не реализован",
        mode=check.mode,
        source="—",
    )