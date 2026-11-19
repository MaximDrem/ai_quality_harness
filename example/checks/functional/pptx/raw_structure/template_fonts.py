"""Проверка M3: шрифты текста финала не выходят за шрифты слайдов шаблона."""

from __future__ import annotations

from typing import List, Optional

from example.checks.functional.pptx._pptx_helpers import ok, unavailable
from example.checks.functional.pptx._template_helpers import (
    explicit_fonts,
    template_and_final,
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
        foreign = sorted(explicit_fonts(final) - explicit_fonts(template))
    except Exception as error:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать PPTX: {error}", artifacts, photos, user_phrase)
    if foreign:
        return unavailable(
            check,
            "Шрифты текста отсутствуют на слайдах шаблона: " + ", ".join(foreign[:10]),
            artifacts,
            photos,
            user_phrase,
        )
    return ok(check, "Шрифты текста встречаются на слайдах шаблона", artifacts, photos, user_phrase)
