from __future__ import annotations

from example.checks.functional.office.processor import Processor as OfficeProcessor


class Processor(OfficeProcessor):
    extensions = {".pptx"}
