"""Проверка M3: размер слайда финала совпадает с шаблоном."""

from __future__ import annotations

from typing import List, Optional

from example.checks.functional.pptx._pptx_helpers import ok, unavailable
from example.checks.functional.pptx._template_helpers import template_and_final
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
        template_size = (template.raw_structure.slide_width, template.raw_structure.slide_height)
        final_size = (final.raw_structure.slide_width, final.raw_structure.slide_height)
    except Exception as error:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать PPTX: {error}", artifacts, photos, user_phrase)
    if template_size != final_size:
        return unavailable(
            check,
            f"Размер слайда {final_size[0]}×{final_size[1]} EMU не совпадает "
            f"с шаблоном {template_size[0]}×{template_size[1]} EMU",
            artifacts,
            photos,
            user_phrase,
        )
    return ok(check, "Размер слайда совпадает с шаблоном", artifacts, photos, user_phrase)
