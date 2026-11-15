"""Cookie-строка для запросов прокси к GigaChat gateway."""
from __future__ import annotations

import json
import re
from typing import Optional


def normalize_cookies(raw: str = "", fallback_json: Optional[dict] = None) -> str:
    """Подготовить строку `Cookie`.

    Принимает: header-строку вида `sid=abc; lang=ru` либо JSON-объект
    `{"sid": "abc"}` (старый формат client). Если ничего — возвращает пусто.
    """
    raw = (raw or "").strip()
    if not raw and fallback_json:
        raw = "; ".join(f"{k}={v}" for k, v in fallback_json.items() if v is not None)
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return "; ".join(f"{k}={v}" for k, v in obj.items() if v is not None)
        except (ValueError, TypeError):
            pass
    parts = [p.strip().strip('"').strip("'") for p in re.split(r"[;]+", raw) if p.strip()]
    return "; ".join(parts)


__all__ = ["normalize_cookies"]