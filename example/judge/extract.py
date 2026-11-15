"""Извлечение текста из Office-артефактов для text-проверок судьи.

Текст достаётся из XML zip-архива (pptx/docx) или через openpyxl (xlsx) с
маркерами границ `=== СЛАЙД N ===` — иначе судья приписывает строки
случайным слайдам. Раны склеиваются без пробела (они режутся посередине
слова), `<a:br>` превращается в пробел — иначе возникают псевдо-опечатки
(«J ava») или склейки («SberOSпередаём»), которые судья приписывает автору.

Для .pptx строка-заголовок слайда (шейп с крупнейшим объявленным кеглем
в верхних ~20% кадра) помечается префиксом `(заголовок)` — в плоской
выгрузке роли строк не видны, и судья ошибочно принимает кикер-надзаголовок
(«Проблема», «План») или крупную цифру-акцент за заголовок слайда.
"""
from __future__ import annotations

import functools
import html
import re
import zipfile
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from openpyxl import load_workbook

from ..cases import vstr


def _xml_text_paragraphs(blob: bytes, para_tag: str, text_tag: str) -> str:
    text = blob.decode("utf-8", errors="ignore")
    lines: List[str] = []
    # (?:\s[^>]*)? — граница тега: <a:p> не цепляет <a:pPr>, а <a:t> —
    # <a:tbl>/<a:tr>/<a:tc> и самозакрывающиеся <a:t/>.
    para_re = re.compile(rf"<{para_tag}(?:\s[^>]*)?>.*?</{para_tag}>", re.S)
    text_re = re.compile(rf"<{text_tag}(?:\s[^>]*)?>(.*?)</{text_tag}>", re.S)

    for para in para_re.findall(text):
        # Мягкий перенос <a:br> стоит между ранами; подменяем его на пробел
        # внутри рана, иначе слова склеятся («SberOSпередаём»).
        br_tag = text_tag.split(":")[0] + ":br"
        para = re.sub(rf"<{br_tag}(?:\s[^>]*)?/?>",
                      f"<{text_tag}> </{text_tag}>", para)
        fragments = [html.unescape(f) for f in text_re.findall(para)]
        # Раны склеиваем без пробела: они режутся посередине слова, пробелы
        # между словами уже внутри текста рана. Иначе получаются «J ava».
        line = "".join(fragments)
        # Схлопнуть повторные пробелы на стыках ран и подменённых <a:br>.
        line = re.sub(r"[ \t]{2,}", " ", line)
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)


def _parse_zip_text(
    path: Path,
    prefix: str,
    suffix: str,
    para_tag: str,
    text_tag: str,
    part_text_fn: Optional[Callable[[bytes], str]] = None,
) -> str:
    parts: List[Tuple[int, str]] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.startswith(prefix) and name.endswith(suffix):
                m = re.search(r"(\d+)" + re.escape(suffix) + r"$", name)
                order = int(m.group(1)) if m else 0
                extract = part_text_fn or _xml_text_paragraphs
                parts.append((order, extract(zf.read(name))))
    out: List[str] = []
    # Натуральный порядок слайдов (slide10 после slide9) + маркеры границ,
    # чтобы судья не путал, какой строке какой слайд принадлежит.
    multi = sum(1 for _o, t in parts if t.strip()) > 1
    for order, text in sorted(parts):
        if not text.strip():
            continue
        out.append(f"=== СЛАЙД {order} ===\n{text}"
                   if multi and order else text)
    return "\n".join(out)


# Блок шейпа pptx, объявленный кегль ранов (сотые пункта, sz="2400" = 24pt)
# и вертикальная позиция шейпа.
_SP_BLOCK = re.compile(r"<p:sp(?:\s[^>]*)?>.*?</p:sp>", re.S)
_SZ_ATTR = re.compile(r'<a:rPr[^>]*\bsz="(\d+)"')
_OFF_Y_ATTR = re.compile(r'<a:off(?:\s[^>]*)?\sy="(-?\d+)"')
_SLDSZ_CY = re.compile(r'<p:sldSz[^>]*\bcy="(\d+)"')

# Префикс-метка строки-заголовка слайда в текстовой выгрузке.
HEADER_MARK = "(заголовок)"

# Заголовок — шейп с текстом не короче этого; отсекает крупные цифры-акценты
# («30%», «×32») и метки-одиночки, у которых роль заголовка неочевидна.
_HEADER_MIN_CHARS = 8
# Заголовок лежит в верхних ~20% кадра; крупная цифра-акцент в середине
# слайда заголовком не считается, какой бы кегль у неё ни был.
_HEADER_TOP_FRAC = 0.2


def _pptx_slide_height(zf: zipfile.ZipFile) -> int:
    """Высота слайда в EMU из presentation.xml (дефолт 7,5")."""
    try:
        m = _SLDSZ_CY.search(zf.read("ppt/presentation.xml").decode("utf-8", "ignore"))
        return int(m.group(1)) if m else 6858000
    except KeyError:
        return 6858000


def _pptx_header_first_line(blob: bytes, slide_h_emu: int) -> Optional[str]:
    """Первая строка шейпа-заголовка слайда.

    Заголовок — шейп с крупнейшим объявленным кеглем среди шейпов верха
    кадра (<= _HEADER_TOP_FRAC высоты) с текстом достаточной длины. None,
    если подходящих шейпов нет — метка не ставится, и проверки определяют
    заголовок по виду строки.
    """
    best = None  # (sz, top, line)
    for block in _SP_BLOCK.findall(blob.decode("utf-8", errors="ignore")):
        text = _xml_text_paragraphs(block.encode("utf-8"), "a:p", "a:t")
        if len(text.strip()) < _HEADER_MIN_CHARS:
            continue
        sizes = [int(m) for m in _SZ_ATTR.findall(block)]
        if not sizes:
            continue
        y = _OFF_Y_ATTR.search(block)
        if y is None:
            continue
        top = int(y.group(1))
        if top > _HEADER_TOP_FRAC * slide_h_emu:
            continue  # середина/низ кадра — акцент, не заголовок
        cand = (max(sizes), -top, text.split("\n", 1)[0].strip())
        if best is None or cand[:2] > best[:2]:
            best = cand
    return best[2] if best else None


def _pptx_slide_text(blob: bytes, slide_h_emu: int) -> str:
    """Текст слайда pptx с меткой `(заголовок)` у строки-заголовка."""
    text = _xml_text_paragraphs(blob, "a:p", "a:t")
    header = _pptx_header_first_line(blob, slide_h_emu)
    if not header:
        return text
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == header:
            lines[i] = f"{HEADER_MARK} {line}"
            break
    return "\n".join(lines)


def _parse_xlsx_text(path: Path) -> str:
    wb = load_workbook(path, data_only=True)
    lines: List[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            vals = [vstr(c) for c in row if c is not None and str(c).strip()]
            if vals:
                lines.append(" | ".join(vals[0:10]))  # не раздувать строку
    return "\n".join(lines)


# Как устроен текст внутри артефакта каждого типа:
# (префикс xml-частей, суффикс, тег абзаца, тег текста).
_OFFICE_XML_TEXT_PARTS = {
    ".pptx": ("ppt/slides/slide", ".xml", "a:p", "a:t"),
    ".docx": ("word/document", ".xml", "w:p", "w:t"),
}


def _parse_office_artifact_text(artifact_path: str) -> str:
    p = Path(artifact_path)
    if not p.exists() or not p.is_file():
        return ""
    suffix = p.suffix.lower()
    if suffix == ".xlsx":
        try:
            return _parse_xlsx_text(p)
        except Exception:
            return ""
    spec = _OFFICE_XML_TEXT_PARTS.get(suffix)
    if spec:
        try:
            if suffix == ".pptx":
                # С меткой заголовка: роли строк в плоской выгрузке не видны.
                with zipfile.ZipFile(p) as zf:
                    slide_h = _pptx_slide_height(zf)
                part_fn = functools.partial(_pptx_slide_text, slide_h_emu=slide_h)
                return _parse_zip_text(p, *spec, part_text_fn=part_fn)
            return _parse_zip_text(p, *spec)
        except Exception:
            return ""
    return ""


def parse_artifact_text(artifact_path: str, processor: str = "") -> str:
    from ..processors import get_processor

    return get_processor(processor, artifact_path).extract_text(artifact_path)


def parse_artifacts_text(artifacts: Sequence[str], processor: str = "", context=None) -> str:
    """Собрать текст из всех артефактов (для текстовой проверки)."""
    chunks: List[str] = []
    for a in artifacts:
        if context is not None:
            t = context.extract_text(a, processor)
        else:
            t = parse_artifact_text(a, processor=processor)
        if t.strip():
            chunks.append(f"--- {Path(a).name} ---\n{t}")
    return "\n\n".join(chunks)