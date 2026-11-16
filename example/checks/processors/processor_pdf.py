"""Процессор PDF: Office-конвейер рендера страниц."""

from ._office import OfficeProcessor


class Processor(OfficeProcessor):
    extensions = {".pdf"}
