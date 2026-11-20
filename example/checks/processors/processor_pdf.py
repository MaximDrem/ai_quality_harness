"""Процессор PDF: текст не извлекается, страницы рендерятся."""

from __future__ import annotations

from ._office import OfficeProcessor


class Processor(OfficeProcessor):
    extensions = {".pdf"}

    def build_text(self, artifact) -> str:
        return ""
