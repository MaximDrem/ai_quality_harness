"""Разбор заголовка промежуточного STYLE.md."""

from __future__ import annotations

import re
from typing import Optional

HEADER_RE = re.compile(
    r"^#\s*STYLE\.md\s*[—–-]\s*(?P<name>.+?)\s*[—–-]\s*\d+\s+slides\b",
    re.IGNORECASE | re.MULTILINE,
)


def declared_template_name(text: str) -> Optional[str]:
    match = HEADER_RE.search(text or "")
    return match.group("name").strip() if match else None
