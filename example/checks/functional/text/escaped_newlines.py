from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from example.checks.functional._helpers import ok, unavailable
from example.models import ArtifactContext, Check, CheckResult
from example.processors import get_processor

_LITERAL_NL = re.compile(r"\\n")


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
        match = _LITERAL_NL.search(text)
        if match:
            start = max(0, match.start() - 12)
            fragment = text[start:match.end() + 8].replace("\n", " ")
            hits.append(f"{Path(artifact).name} («{fragment}…»)")
    if not hits:
        return ok(check, "Буквальных «\\n» в тексте нет", artifacts, photos, user_phrase)
    return unavailable(
        check,
        "Буквальные «\\n» вместо переноса строки: " + ", ".join(hits[:10]),
        artifacts,
        photos,
        user_phrase,
    )
