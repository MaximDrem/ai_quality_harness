"""Проверка M3: медиафайлы финальной презентации взяты из шаблона."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Optional
from zipfile import ZipFile

from example.checks.functional.pptx._pptx_helpers import ok, unavailable
from example.checks.functional.pptx._template_helpers import template_and_final
from example.models import Artifact, Check, CheckResult


def _media_digests(path: Path) -> Dict[str, str]:
    with ZipFile(path) as archive:
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if name.startswith("ppt/media/") and not name.endswith("/")
        }


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
        template_media = _media_digests(Path(template.path))
        final_media = _media_digests(Path(final.path))
    except Exception as error:  # noqa: BLE001
        return unavailable(check, f"Не удалось прочитать PPTX: {error}", artifacts, photos, user_phrase)
    allowed = set(template_media.values())
    foreign = [name for name, digest in final_media.items() if digest not in allowed]
    if foreign:
        return unavailable(
            check,
            "Медиафайлы отсутствуют в шаблоне: " + ", ".join(foreign),
            artifacts,
            photos,
            user_phrase,
        )
    return ok(check, "Все медиафайлы финальной презентации содержатся в шаблоне", artifacts, photos, user_phrase)
