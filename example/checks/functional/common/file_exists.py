from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from example.checks.functional._helpers import ok, unavailable
from example.models import Check, CheckResult


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    if not artifacts:
        return unavailable(check, "Артефакт не создан агентом (файл не найден)", artifacts, photos, user_phrase)
    missing = [path for path in artifacts if not Path(path).is_file()]
    if missing:
        return unavailable(check, "Файлы не найдены: " + ", ".join(missing), artifacts, photos, user_phrase)
    return ok(check, f"Все файлы существуют: {len(artifacts)}", artifacts, photos, user_phrase)
