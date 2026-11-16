"""Поиск удвоенных слов в Artifact.text без пересечения абзацев."""

from __future__ import annotations

import re

from example.checks._helpers import ok, unavailable

_DOUBLE = re.compile(r"\b([A-Za-zА-Яа-яЁё]{2,})(?:[ \t\u00A0]+)\1\b", re.IGNORECASE)


def run(check, artifacts, user_phrase=None, context=None):
    if not artifacts:
        return unavailable(check, "Нет артефактов для текстовой проверки", artifacts, user_phrase)
    hits = []
    for artifact in artifacts:
        for match in _DOUBLE.finditer(artifact.text):
            label = artifact.path or artifact.name
            hits.append(f"{label} («{match.group(1)} {match.group(1)}»)")
    if not hits:
        return ok(check, "Удвоенных слов нет", artifacts, user_phrase)
    return unavailable(check, "Удвоенные слова: " + ", ".join(hits[:10]), artifacts, user_phrase)
