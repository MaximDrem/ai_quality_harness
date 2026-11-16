"""Процессор PPTX: текст, Presentation в raw_structure и рендер страниц."""

from __future__ import annotations

import json

from pptx import Presentation

from ._office import OfficeProcessor


class Processor(OfficeProcessor):
    extensions = {".pptx"}

    def build_raw_structure(self, artifact):
        return Presentation(artifact.path)

    def serialize_raw_structure(self, raw_structure) -> str:
        slides = []
        for slide in raw_structure.slides:
            shapes = []
            for shape in slide.shapes:
                shapes.append({
                    "name": shape.name,
                    "type": str(shape.shape_type),
                    "text": shape.text if getattr(shape, "has_text_frame", False) else "",
                    "left": int(shape.left) if shape.left is not None else None,
                    "top": int(shape.top) if shape.top is not None else None,
                    "width": int(shape.width) if shape.width is not None else None,
                    "height": int(shape.height) if shape.height is not None else None,
                })
            slides.append({"shapes": shapes})
        return json.dumps({"slides": slides}, ensure_ascii=False)
