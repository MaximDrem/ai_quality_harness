from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from example.checks.functional._helpers import ok, unavailable
from example.models import ArtifactContext, Check, CheckResult


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
    context: Optional[ArtifactContext] = None,
) -> CheckResult:
    if not artifacts:
        return unavailable(check, "Артефакт не создан агентом (файл не найден)", artifacts, photos, user_phrase)
    if context is None:
        if not photos:
            return unavailable(check, "Артефакт не удалось открыть/отрендерить (нет PNG)", artifacts, photos, user_phrase)
    else:
        failed = [path for path in artifacts if not context.photos.get(path)]
        if failed:
            names = ", ".join(Path(path).name for path in failed)
            return unavailable(check, f"Файлы не удалось отрендерить: {names}", artifacts, photos, user_phrase)
    return ok(check, f"Все артефакты отрендерены: {len(artifacts)}", artifacts, photos, user_phrase)
