"""Процессор текстовых файлов; JSON дополнительно даёт dict/list в raw_structure."""

from __future__ import annotations

import json
from pathlib import Path

from .base import BaseProcessor


class Processor(BaseProcessor):
    extensions = {".md", ".txt", ".json", ".csv", ".html", ".svg", ".log"}

    def build_text(self, artifact) -> str:
        return Path(artifact.path).read_text(encoding="utf-8", errors="replace")

    def build_raw_structure(self, artifact):
        artifact.text = self.build_text(artifact)
        artifact.loaded_attributes.add("text")
        if Path(artifact.path).suffix.lower() == ".json":
            return json.loads(artifact.text)
        return None

    def build_rendered_photos(self, artifact) -> list[str]:
        return []
