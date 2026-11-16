"""Проверка существования каждого выбранного Artifact.path."""

from __future__ import annotations

from pathlib import Path

from example.checks._helpers import ok, unavailable


def run(check, artifacts, user_phrase=None, context=None):
    if not artifacts:
        return unavailable(check, "Артефакт не создан агентом (файл не найден)", artifacts, user_phrase)
    missing = [artifact.path for artifact in artifacts if not artifact.path or not Path(artifact.path).is_file()]
    if missing:
        return unavailable(check, "Файлы не найдены: " + ", ".join(missing), artifacts, user_phrase)
    return ok(check, f"Все файлы существуют: {len(artifacts)}", artifacts, user_phrase)
