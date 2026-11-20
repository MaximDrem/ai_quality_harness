"""Процессор DOCX: параграфы документа и Office-рендер."""

from __future__ import annotations

from pathlib import Path

from ._office import OfficeProcessor
from ._office_xml import parse_zip_text


class Processor(OfficeProcessor):
    extensions = {".docx"}

    def build_text(self, artifact) -> str:
        return parse_zip_text(
            Path(artifact.path),
            "word/document",
            ".xml",
            "w:p",
            "w:t",
        )
