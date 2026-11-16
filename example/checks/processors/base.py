"""Базовый контракт ленивой материализации атрибутов Artifact.

Подклассы реализуют build_text/build_raw_structure/build_rendered_photos;
process кэширует результат через artifact.loaded_attributes, а
serialize_raw_structure подготавливает JSON-совместимый вход для judge.
"""

from __future__ import annotations

import json
from pathlib import Path


class BaseProcessor:
    extensions = set()

    def serialize_raw_structure(self, raw_structure) -> str:
        if raw_structure is None:
            return ""
        return json.dumps(raw_structure, ensure_ascii=False, allow_nan=False)

    def process(self, artifact, attributes: set[str]) -> None:
        for attribute in attributes:
            if attribute in artifact.loaded_attributes:
                continue
            if attribute == "path":
                artifact.loaded_attributes.add(attribute)
                continue
            builder = getattr(self, f"build_{attribute}", None)
            if builder is None:
                raise ValueError(
                    f"Процессор {type(self).__name__} не поддерживает атрибут {attribute!r}"
                )
            if not artifact.path:
                raise ValueError(f"У артефакта {artifact.name!r} нет пути для процессора")
            if not Path(artifact.path).is_file():
                raise FileNotFoundError(f"Артефакт не найден: {artifact.path}")
            setattr(artifact, attribute, builder(artifact))
            artifact.loaded_attributes.add(attribute)
