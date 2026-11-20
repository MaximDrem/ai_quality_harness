"""Трейс API-запросов к upstream (для raw.log кейса).

Слой не зависит от opencode: просто описывает факт обращения к upstream и его
ответ — статус, тип, сниппет, распознанные поля (модель, промпт, ошибка).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RESP_SNIPPET_BYTES = 4000
RESP_SNIPPET_TEXT = 800

# Дамп полного тела запроса (для сверки системного промта с desktop).
# Файл на уникальное тело: имя — первые 12 hex sha1, поэтому повторы
# (retry, поллинг) перезаписывают один и тот же файл.
DUMP_DIR_NAME = "requests"
_DUMP_HASH_CHARS = 12

_IMAGE_UUID_RE = re.compile(
    r'<img\s+src="([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"',
    re.IGNORECASE,
)

# Промпт и сниппеты уходят в raw.log, который лежит рядом с артефактами кейса.
_PROMPT_SNIPPET = 300
_CONTENT_SNIPPET = 300


def _decode_json(body: Optional[bytes]) -> Optional[Any]:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except (ValueError, TypeError):
        return None


def build_request_trace(method: str, local_path: str, target: str, body: Optional[bytes]) -> Dict:
    """Нормализованное описание исходящего запроса (без секретов)."""
    rec: Dict = {
        "ts": time.time(),
        "method": method,
        "path": local_path,
        "upstream": target,
    }
    local = local_path.split("?", 1)[0].rstrip("/")
    rec["is_models"] = local == "/models"
    rec["is_files"] = local.startswith("/files")
    obj = _decode_json(body)
    if not isinstance(obj, dict):
        return rec
    rec["model"] = obj.get("model")
    fn_names: List[str] = []
    funcs = obj.get("functions")
    if isinstance(funcs, list):
        fn_names = [f.get("name") for f in funcs if isinstance(f, dict) and f.get("name")]
    rec["functions"] = fn_names or None
    rec["stream"] = obj.get("stream")
    # `text2image` в списке доступных тулов чата (не обязательно реальный вызов).
    image_tool_available = "text2image" in fn_names
    rec["image_tool_available"] = image_tool_available or None
    # Реальный вызов генерации картинки = ровно как buildImageBody тула:
    # stream:false + единичный functions:[{name:"text2image"}], function_call:auto.
    is_image = (
        rec["stream"] is False
        and fn_names == ["text2image"]
        and obj.get("function_call") in ("auto", "text2image", None)
    )
    rec["is_image"] = is_image
    # Последний user-текст (промпт), обрезанный.
    msgs = obj.get("messages")
    prompt = None
    if isinstance(msgs, list):
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    prompt = content[:_PROMPT_SNIPPET]
                    break
    rec["prompt"] = prompt
    return rec


def apply_response_trace(rec: Dict, status: int, content_type: str, resp_body: Optional[bytes]) -> None:
    """Отдать факт ответа upstream: статус, тип, сниппет и распознанные поля."""
    rec["status"] = status
    rec["content_type"] = content_type
    if not resp_body:
        return
    text = resp_body.decode("utf-8", "replace")
    rec.setdefault("body_snippet", text[:RESP_SNIPPET_TEXT])
    img = _IMAGE_UUID_RE.search(text)
    if img:
        rec["image_uuid"] = img.group(1)
    obj = _decode_json(resp_body)
    if isinstance(obj, dict):
        err = obj.get("error")
        if isinstance(err, dict) and err.get("detail"):
            rec["error"] = err["detail"]
        elif isinstance(err, str):
            rec["error"] = err
        choices = obj.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, str) and content:
                rec["content_snippet"] = content[:_CONTENT_SNIPPET]


def finish_trace(rec: Dict, t0: float) -> Dict:
    """Дописать задержку запроса (мс) и вернуть запись."""
    rec["latency_ms"] = round((time.time() - t0) * 1000.0, 1)
    return rec


def dump_request_body(directory, body: Optional[bytes]) -> str:
    """Записать тело запроса в `<directory>/<sha1-12>.json`, вернуть путь.

    Сниппетов в raw.log недостаточно, чтобы сверить системный промт с
    desktop: режем по 300 символов. Здесь тело сохраняется целиком (это
    ровно то, что ушло в upstream), а дедуп по содержимому не даёт копиться
    одинаковым файлам. Ошибки записи не должны ломать запрос — возвращаем "".
    """
    if not body:
        return ""
    try:
        digest = hashlib.sha1(body).hexdigest()[:_DUMP_HASH_CHARS]
        target = Path(directory) / f"{digest}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    except OSError:
        return ""
    return str(target)


def browser_origin_referer(upstream: str) -> Tuple[str, str]:
    """(Origin, Referer) от origin upstream — replay браузерных заголовков,
    снятых при Sudir-логине (см. sudir-auth.ts / apiClient.ts)."""
    try:
        p = urllib.parse.urlsplit(upstream)
        return f"{p.scheme}://{p.netloc}", upstream
    except ValueError:
        return "", ""


__all__ = [
    "DUMP_DIR_NAME",
    "RESP_SNIPPET_BYTES",
    "RESP_SNIPPET_TEXT",
    "build_request_trace",
    "apply_response_trace",
    "dump_request_body",
    "finish_trace",
    "browser_origin_referer",
]