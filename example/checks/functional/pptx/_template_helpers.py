"""Общие helpers проверок сценария клонирования PPTX-шаблона."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple
from zipfile import ZipFile

from lxml import etree
from pptx.enum.dml import MSO_COLOR_TYPE

from example.models import Artifact

MASTER_THEME_PARTS = ("ppt/slideMasters/", "ppt/theme/")
LAYOUT_PARTS = ("ppt/slideLayouts/",)
SLIDE_TEXT_PARTS = ("ppt/slides/", "ppt/notesSlides/")
METADATA_PARTS = ("docProps/",)
_THEME_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"


def is_pptx(artifact: Artifact) -> bool:
    return (artifact.artifact_type or Path(artifact.path or "").suffix.lower()) == ".pptx"


def template_and_final(artifacts: Sequence[Artifact]) -> Tuple[Optional[Artifact], Optional[Artifact]]:
    template = next(
        (artifact for artifact in artifacts if artifact.role == "source" and is_pptx(artifact)),
        None,
    )
    final = next(
        (artifact for artifact in artifacts if artifact.role == "final" and is_pptx(artifact)),
        None,
    )
    return template, final


def final_presentation(artifacts: Sequence[Artifact]) -> Optional[Artifact]:
    return next(
        (
            artifact
            for artifact in artifacts
            if artifact.role == "final" and is_pptx(artifact)
        ),
        None,
    )


def _normalized_text(value: Optional[str]) -> str:
    return " ".join((value or "").split())


def element_digest(element) -> str:
    """Подпись XML без зависимости от префиксов пространств имён."""
    digest = hashlib.sha256()

    def _feed(node) -> None:
        try:
            name = etree.QName(node)
            namespace = str(name.namespace or "")
            local = str(name.localname)
        except (TypeError, ValueError):
            namespace, local = "", str(node.tag)
        digest.update(f"<{namespace}:{local}".encode("utf-8"))
        digest.update(_normalized_text(node.text).encode("utf-8"))
        attributes = []
        for key, value in node.attrib.items():
            try:
                attribute = etree.QName(key)
                attribute_name = f"{attribute.namespace or ''}:{attribute.localname}"
            except (TypeError, ValueError):
                attribute_name = str(key)
            attributes.append((attribute_name, str(value)))
        for name, value in sorted(attributes):
            digest.update(f" {name}={value!r}".encode("utf-8"))
        digest.update(b">")
        for child in node.iterchildren(etree.Element):
            _feed(child)
            digest.update(_normalized_text(child.tail).encode("utf-8"))
        digest.update(b"</>")

    _feed(element)
    return digest.hexdigest()


def part_digests(path: Path, prefixes: Sequence[str]) -> Counter[str]:
    digests: Counter[str] = Counter()
    with ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith(".xml") or not name.startswith(tuple(prefixes)):
                continue
            try:
                root = etree.fromstring(archive.read(name))
            except etree.XMLSyntaxError as error:
                raise ValueError(f"{name}: некорректный XML: {error}") from error
            digests[element_digest(root)] += 1
    return digests


def layout_digests(prs) -> List[str]:
    return [
        element_digest(layout.element)
        for master in prs.slide_masters
        for layout in master.slide_layouts
    ]


def _iter_shapes(shapes: Iterable):
    for shape in shapes:
        yield shape
        nested = getattr(shape, "shapes", None)
        if nested is not None:
            yield from _iter_shapes(nested)


def explicit_fonts(artifact: Artifact) -> Set[str]:
    fonts: Set[str] = set()
    for slide in artifact.raw_structure.slides:
        for shape in _iter_shapes(slide.shapes):
            frames = []
            if getattr(shape, "has_text_frame", False):
                frames.append(shape.text_frame)
            if getattr(shape, "has_table", False):
                frames.extend(cell.text_frame for row in shape.table.rows for cell in row.cells)
            for frame in frames:
                for paragraph in frame.paragraphs:
                    for run in paragraph.runs:
                        if run.font.name:
                            fonts.add(run.font.name.casefold())
    return fonts


def explicit_text_colors(artifact: Artifact) -> Set[str]:
    colors: Set[str] = set()
    for slide in artifact.raw_structure.slides:
        for shape in _iter_shapes(slide.shapes):
            frames = []
            if getattr(shape, "has_text_frame", False):
                frames.append(shape.text_frame)
            if getattr(shape, "has_table", False):
                frames.extend(cell.text_frame for row in shape.table.rows for cell in row.cells)
            for frame in frames:
                for paragraph in frame.paragraphs:
                    for run in paragraph.runs:
                        try:
                            if run.font.color.type == MSO_COLOR_TYPE.RGB:
                                colors.add(str(run.font.color.rgb).upper())
                        except (AttributeError, NotImplementedError, ValueError):
                            continue
    return colors


def used_theme_colors(artifact: Artifact) -> Set[str]:
    colors: Set[str] = set()
    seen_masters = set()
    for slide in artifact.raw_structure.slides:
        master = slide.slide_layout.slide_master
        part_name = str(master.part.partname)
        if part_name in seen_masters:
            continue
        seen_masters.add(part_name)
        for relation in master.part.rels.values():
            if relation.reltype != _THEME_REL:
                continue
            try:
                root = etree.fromstring(relation.target_part.blob)
                scheme = root.find(
                    ".//{http://schemas.openxmlformats.org/drawingml/2006/main}clrScheme"
                )
            except (etree.XMLSyntaxError, AttributeError):
                continue
            if scheme is None:
                continue
            for element in scheme.iter():
                try:
                    local = etree.QName(element).localname
                except (TypeError, ValueError):
                    continue
                value = ""
                if local == "srgbClr":
                    value = element.get("val") or ""
                elif local == "sysClr":
                    value = element.get("lastClr") or ""
                if value:
                    colors.add(value.upper())
    return colors


def template_fonts(path: Path) -> Set[str]:
    fonts: Set[str] = set()
    with ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.endswith((".xml", ".rels")):
                continue
            try:
                root = etree.fromstring(archive.read(name))
            except etree.XMLSyntaxError:
                continue
            for element in root.iter():
                try:
                    local = etree.QName(element).localname
                except (TypeError, ValueError):
                    continue
                if local == "latin":
                    typeface = element.get("typeface")
                    if typeface:
                        fonts.add(typeface.casefold())
    return fonts


def forbidden_patterns(check) -> List[Tuple[str, re.Pattern[str]]]:
    result: List[Tuple[str, re.Pattern[str]]] = []
    for term in check.options.get("terms") or []:
        value = str(term or "").strip()
        if value:
            result.append((value, re.compile(re.escape(value), re.IGNORECASE)))
    for pattern in check.options.get("patterns") or []:
        value = str(pattern or "").strip()
        if value:
            result.append((value, re.compile(value)))
    return result


def part_texts(archive: ZipFile, name: str) -> List[str]:
    try:
        root = etree.fromstring(archive.read(name))
    except etree.XMLSyntaxError:
        return []
    values = [value for value in root.itertext() if value and value.strip()]
    for element in root.iter():
        values.extend(str(value) for value in element.attrib.values() if value)
    values.append(" ".join(value.strip() for value in values if value.strip()))
    return values


def public_part_name(name: str) -> str:
    return name.removeprefix("ppt/").removeprefix("docProps/")


def find_forbidden(
    path: Path,
    check,
    part_filter: Callable[[str], bool],
) -> List[str]:
    matches: List[str] = []
    with ZipFile(path) as archive:
        for name in archive.namelist():
            if not part_filter(name):
                continue
            haystacks = part_texts(archive, name)
            for label, pattern in forbidden_patterns(check):
                if any(pattern.search(value) for value in haystacks):
                    matches.append(f"{label!r} в {public_part_name(name)}")
    return matches
