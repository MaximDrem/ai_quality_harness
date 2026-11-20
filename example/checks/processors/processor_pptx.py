"""Процессор PPTX: текст слайдов, Presentation и рендер страниц."""

from __future__ import annotations

import functools
import json
import re
import zipfile
from pathlib import Path
from typing import Optional

from pptx import Presentation

from ._office import OfficeProcessor
from ._office_xml import parse_zip_text, xml_text_paragraphs

_SP_BLOCK = re.compile(r"<p:sp(?:\s[^>]*)?>.*?</p:sp>", re.S)
_SZ_ATTR = re.compile(r'<a:rPr[^>]*\bsz="(\d+)"')
_OFF_Y_ATTR = re.compile(r'<a:off(?:\s[^>]*)?\sy="(-?\d+)"')
_SLDSZ_CY = re.compile(r'<p:sldSz[^>]*\bcy="(\d+)"')
_HEADER_MIN_CHARS = 8
_HEADER_TOP_FRAC = 0.2


def _slide_height(archive: zipfile.ZipFile) -> int:
    try:
        match = _SLDSZ_CY.search(archive.read("ppt/presentation.xml").decode("utf-8", "ignore"))
        return int(match.group(1)) if match else 6858000
    except KeyError:
        return 6858000


def _header_first_line(blob: bytes, slide_h_emu: int) -> Optional[str]:
    best = None
    for block in _SP_BLOCK.findall(blob.decode("utf-8", errors="ignore")):
        text = xml_text_paragraphs(block.encode("utf-8"), "a:p", "a:t")
        if len(text.strip()) < _HEADER_MIN_CHARS:
            continue
        sizes = [int(match) for match in _SZ_ATTR.findall(block)]
        if not sizes:
            continue
        position = _OFF_Y_ATTR.search(block)
        if position is None:
            continue
        top = int(position.group(1))
        if top > _HEADER_TOP_FRAC * slide_h_emu:
            continue
        candidate = (max(sizes), -top, text.split("\n", 1)[0].strip())
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best else None


def _slide_text(blob: bytes, slide_h_emu: int) -> str:
    text = xml_text_paragraphs(blob, "a:p", "a:t")
    header = _header_first_line(blob, slide_h_emu)
    if not header:
        return text
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.strip() == header:
            lines[index] = f"(заголовок) {line}"
            break
    return "\n".join(lines)


class Processor(OfficeProcessor):
    extensions = {".pptx"}

    def build_text(self, artifact) -> str:
        path = Path(artifact.path)
        with zipfile.ZipFile(path) as archive:
            slide_height = _slide_height(archive)
        part_fn = functools.partial(_slide_text, slide_h_emu=slide_height)
        return parse_zip_text(
            path,
            "ppt/slides/slide",
            ".xml",
            "a:p",
            "a:t",
            part_text_fn=part_fn,
        )

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
