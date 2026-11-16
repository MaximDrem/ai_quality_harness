"""Поиск буквальных последовательностей ``\\n`` в Artifact.text."""

from __future__ import annotations

import re

from example.checks._helpers import ok, unavailable

_LITERAL_NL = re.compile(r"\\n")


def run(check, artifacts, user_phrase=None, context=None):
    if not artifacts:
        return unavailable(check, "Нет артефактов для текстовой проверки", artifacts, user_phrase)
    hits = []
    for artifact in artifacts:
        match = _LITERAL_NL.search(artifact.text)
        if match:
            start = max(0, match.start() - 12)
            fragment = artifact.text[start:match.end() + 8].replace("\n", " ")
            hits.append(f"{artifact.path or artifact.name} («{fragment}…»)")
    if not hits:
        return ok(check, "Буквальных «\\n» в тексте нет", artifacts, user_phrase)
    return unavailable(
        check,
        "Буквальные «\\n» вместо переноса строки: " + ", ".join(hits[:10]),
        artifacts,
        user_phrase,
    )
