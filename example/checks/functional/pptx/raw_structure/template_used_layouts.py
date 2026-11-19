"""Проверка M3: каждый использованный слайдом layout есть в шаблоне."""

from __future__ import annotations

from typing import List, Optional

from example.checks.functional.pptx._pptx_helpers import ok, unavailable
from example.checks.functional.pptx._template_helpers import (
    element_digest,
    layout_digests,
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
        expected = set(layout_digests(template.raw_structure))
        foreign = [
            f"слайд {index} ({slide.slide_layout.name})"
            for index, slide in enumerate(final.raw_structure.slides, start=1)
            if element_digest(slide.slide_layout.element) not in expected
        ]
    except Exception as error:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать PPTX: {error}", artifacts, photos, user_phrase)
    if foreign:
        return unavailable(
            check,
            "Использованы чужие или изменённые layouts: " + ", ".join(foreign[:5]),
            artifacts,
            photos,
            user_phrase,
        )
    return ok(check, "Все использованные layouts есть в шаблоне", artifacts, photos, user_phrase)
