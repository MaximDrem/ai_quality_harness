"""Проверка наличия завершённого вызова инструмента в Artifact.tool_calls."""

from __future__ import annotations

from example.checks._helpers import ok, unavailable


def run(check, artifacts, user_phrase=None, context=None):
    tool = str(check.options.get("tool") or check.options.get("name") or "").strip()
    state = str(check.options.get("state") or "completed").strip()
    if not tool:
        return unavailable(check, "Задайте options.tool для проверки", artifacts, user_phrase)
    matches = [
        call
        for artifact in artifacts
        for call in artifact.tool_calls
        if call.get("tool") == tool and call.get("state") == state
    ]
    if matches:
        return ok(check, f"Инструмент {tool}: вызовов {len(matches)}", artifacts, user_phrase)
    return unavailable(check, f"Завершённый вызов {tool} не найден", artifacts, user_phrase)
