"""Процессор XLSX: значения ячеек и Office-рендер."""

from __future__ import annotations

from openpyxl import load_workbook

from ._office import OfficeProcessor
from ._office_xml import value_text


class Processor(OfficeProcessor):
    extensions = {".xlsx"}

    def build_text(self, artifact) -> str:
        workbook = load_workbook(artifact.path, data_only=True)
        lines = []
        for worksheet in workbook.worksheets:
            for row in worksheet.iter_rows(values_only=True):
                values = [value_text(cell) for cell in row if cell is not None and str(cell).strip()]
                if values:
                    lines.append(" | ".join(values[0:10]))
        return "\n".join(lines)
