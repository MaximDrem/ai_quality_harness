"""Низкоуровневые helpers чтения XML zip-пакетов для конкретных processors."""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple


def value_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def xml_text_paragraphs(blob: bytes, para_tag: str, text_tag: str) -> str:
    text = blob.decode("utf-8", errors="ignore")
    lines: List[str] = []
    para_re = re.compile(rf"<{para_tag}(?:\s[^>]*)?>.*?</{para_tag}>", re.S)
    text_re = re.compile(rf"<{text_tag}(?:\s[^>]*)?>(.*?)</{text_tag}>", re.S)

    for para in para_re.findall(text):
        br_tag = text_tag.split(":")[0] + ":br"
        para = re.sub(
            rf"<{br_tag}(?:\s[^>]*)?/?>",
            f"<{text_tag}> </{text_tag}>",
            para,
        )
        fragments = [html.unescape(fragment) for fragment in text_re.findall(para)]
        line = "".join(fragments)
        line = re.sub(r"[ \t]{2,}", " ", line)
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)


def parse_zip_text(
    path: Path,
    prefix: str,
    suffix: str,
    para_tag: str,
    text_tag: str,
    part_text_fn: Optional[Callable[[bytes], str]] = None,
) -> str:
    parts: List[Tuple[int, str]] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.startswith(prefix) and name.endswith(suffix):
                match = re.search(r"(\d+)" + re.escape(suffix) + r"$", name)
                order = int(match.group(1)) if match else 0
                extract = part_text_fn or xml_text_paragraphs
                parts.append((order, extract(archive.read(name))))
    output: List[str] = []
    multi = sum(1 for _order, text in parts if text.strip()) > 1
    for order, text in sorted(parts):
        if not text.strip():
            continue
        output.append(f"=== СЛАЙД {order} ===\n{text}" if multi and order else text)
    return "\n".join(output)
