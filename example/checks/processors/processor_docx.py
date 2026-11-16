"""Процессор DOCX: извлечение текста и Office-рендер."""

from ._office import OfficeProcessor


class Processor(OfficeProcessor):
    extensions = {".docx"}
