"""Проверка M3: набор layout-заготовок шаблона сохранён в финале."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from example.checks.functional.pptx._pptx_helpers import ok, unavailable
from example.checks.functional.pptx._template_helpers import (
    LAYOUT_PARTS,
    part_digests,
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
    if template is None or final is None or not template.path or not final.path:
        return unavailable(check, "Нужны шаблон и финальная презентация .pptx", artifacts, photos, user_phrase)
    try:
        expected = part_digests(Path(template.path), LAYOUT_PARTS)
        actual = part_digests(Path(final.path), LAYOUT_PARTS)
    except Exception as error:  # noqa: BLE001
        return unavailable(check, f"Не удалось прочитать PPTX: {error}", artifacts, photos, user_phrase)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        return unavailable(
            check,
            f"Набор layouts отличается от шаблона (не перенесено: {sum(missing.values())}, "
            f"изменено или добавлено: {sum(extra.values())})",
            artifacts,
            photos,
            user_phrase,
        )
    return ok(check, "Набор layouts соответствует шаблону", artifacts, photos, user_phrase)
