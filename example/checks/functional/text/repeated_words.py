from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from example.checks.functional._helpers import ok, unavailable
from example.models import ArtifactContext, Check, CheckResult
from example.processors import get_processor

_DOUBLE = re.compile(r"\b([A-Za-zА-Яа-яЁё]{2,})(?:[ \t\u00A0]+)\1\b", re.IGNORECASE)


def _find_doubles(text: str) -> List[str]:
    return [match.group(1) for match in _DOUBLE.finditer(text)]


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
    context: Optional[ArtifactContext] = None,
) -> CheckResult:
    if not artifacts:
        return unavailable(check, "Нет артефактов для текстовой проверки", artifacts, photos, user_phrase)
    hits: List[str] = []
    for artifact in artifacts:
        try:
            if context is not None:
                text = context.extract_text(artifact, check.processor)
            else:
                text = get_processor(check.processor, artifact).extract_text(artifact)
        except Exception as error:
            return unavailable(check, f"Не удалось извлечь текст из {artifact}: {error}", artifacts, photos, user_phrase)
        for word in _find_doubles(text):
            hits.append(f"{Path(artifact).name} («{word} {word}»)")
    if not hits:
        return ok(check, "Удвоенных слов нет", artifacts, photos, user_phrase)
    return unavailable(
        check,
        "Удвоенные слова: " + ", ".join(hits[:10]),
        artifacts,
        photos,
        user_phrase,
    )
