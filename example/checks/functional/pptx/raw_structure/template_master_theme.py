"""Проверка M3: master и theme перенесены из шаблона без изменений."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from example.checks.functional.pptx._pptx_helpers import ok, unavailable
from example.checks.functional.pptx._template_helpers import (
    MASTER_THEME_PARTS,
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
        expected = part_digests(Path(template.path), MASTER_THEME_PARTS)
        actual = part_digests(Path(final.path), MASTER_THEME_PARTS)
    except Exception as error:  # noqa: BLE001
        return unavailable(check, f"Не удалось прочитать PPTX: {error}", artifacts, photos, user_phrase)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        return unavailable(
            check,
            f"Master/theme отличаются от шаблона (не перенесено: {sum(missing.values())}, "
            f"изменено или добавлено: {sum(extra.values())})",
            artifacts,
            photos,
            user_phrase,
        )
    return ok(check, "Master и theme соответствуют шаблону", artifacts, photos, user_phrase)
