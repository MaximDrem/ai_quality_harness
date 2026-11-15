"""Функциональная проверка `file_exists`: файл-артефакт существует на диске.

Единый интерфейс функциональной проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

from typing import List, Optional

from ...models import Check, CheckResult
from ._helpers import ok, unavailable


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """Проверка существования файла-артефакта на диске."""
    if not artifacts:
        return unavailable(check, "Артефакт не создан агентом (файл не найден)", artifacts, photos, user_phrase)
    return ok(check, "Файл существует на диске", artifacts, photos, user_phrase)