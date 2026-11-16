"""Проверка успешного рендера каждого выбранного артефакта."""

from __future__ import annotations

from pathlib import Path

from example.checks._helpers import ok, unavailable


def run(check, artifacts, user_phrase=None, context=None):
    if not artifacts:
        return unavailable(check, "Артефакт не создан агентом (файл не найден)", artifacts, user_phrase)
    failed = [artifact for artifact in artifacts if not artifact.rendered_photos]
    if failed:
        names = ", ".join(Path(artifact.path).name if artifact.path else artifact.name for artifact in failed)
        return unavailable(check, f"Файлы не удалось отрендерить: {names}", artifacts, user_phrase)
    return ok(check, f"Все артефакты отрендерены: {len(artifacts)}", artifacts, user_phrase)
