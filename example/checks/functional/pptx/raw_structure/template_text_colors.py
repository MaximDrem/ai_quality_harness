"""Проверка M3: цвета текста финала не выходят за палитру шаблона."""

from __future__ import annotations

from typing import List, Optional

from example.checks.functional.pptx._pptx_helpers import ok, unavailable
from example.checks.functional.pptx._template_helpers import (
    explicit_text_colors,
    template_and_final,
    used_theme_colors,
)
from example.models import Artifact, Check, CheckResult


def run(
    check: Check,
    artifacts: List[Artifact],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    template, final = template_and_final(artifacts)
    if template is None or final is None:
        return unavailable(check, "Нужны шаблон и финальная презентация .pptx", artifacts, photos, user_phrase)
    try:
        allowed = explicit_text_colors(template) | used_theme_colors(template)
        foreign = sorted(explicit_text_colors(final) - allowed)
    except Exception as error:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать PPTX: {error}", artifacts, photos, user_phrase)
    if foreign:
        return unavailable(
            check,
            "Цвета текста отсутствуют в слайдах/палитре шаблона: " + ", ".join(f"#{value}" for value in foreign[:10]),
            artifacts,
            photos,
            user_phrase,
        )
    return ok(check, "Цвета текста относятся к слайдам или палитре шаблона", artifacts, photos, user_phrase)
