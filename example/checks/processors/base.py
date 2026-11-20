"""Базовый контракт полного извлечения атрибутов Artifact.

Processor.process вызывается до checks и сразу заполняет все поддерживаемые
text/raw_structure/rendered_photos. Ошибки сохраняются по атрибутам в Artifact,
не отменяя успешно извлечённые остальные данные.
"""

from __future__ import annotations

import json
from pathlib import Path


class BaseProcessor:
    extensions = set()
    attributes = ("text", "raw_structure", "rendered_photos")

    def serialize_raw_structure(self, raw_structure) -> str:
        if raw_structure is None:
            return ""
        return json.dumps(raw_structure, ensure_ascii=False, allow_nan=False)

    def process(self, artifact) -> None:
        artifact.processing_errors = {}
        path = Path(artifact.path or "")
        if not artifact.path or not path.is_file():
            message = f"Артефакт не найден: {artifact.path}"
            artifact.processing_errors = {attribute: message for attribute in self.attributes}
            return
        for attribute in self.attributes:
            builder = getattr(self, f"build_{attribute}", None)
            if builder is None:
                continue
            try:
                setattr(artifact, attribute, builder(artifact))
            except Exception as error:  # noqa: BLE001
                artifact.processing_errors[attribute] = str(error)
        if "raw_structure" not in artifact.processing_errors:
            try:
                artifact.serialized_raw_structure = self.serialize_raw_structure(artifact.raw_structure)
            except Exception as error:  # noqa: BLE001
                artifact.processing_errors["raw_structure"] = str(error)
