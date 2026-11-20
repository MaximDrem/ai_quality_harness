"""Платформенные MCP-серверы desktop-ai-app → `setSystemMcp`.

Desktop читает `packages/desktop-electron/resources/system-mcp.json`
(`system-mcp-config.ts`), подставляет `${VAR}` из окружения и отдаёт записи
sidecar'у через `setInjections({ systemMcp })`. Пока сервер реально подключён,
opencode: строит `<mcp-routing-guidance>`, убирает встроенный `corp_search` из
видимых тулов (он остаётся в отложенном каталоге) и добавляет
`lazy_search_tool`. Без этой инъекции headless-обвязка получает другой
системный промт и другой список функций, чем desktop.

Параметры подключения платформенные: пользовательский конфиг может лишь
выключить сервер (`enabled: false`), поэтому записи помечаются `system: True`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

# `${VAR}` в url — как ENV_PLACEHOLDER в system-mcp-config.ts.
_ENV_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")

# corpsearch подключён «из коробки»: его авторизация — обычная Sudir-сессия,
# отдельного согласия не требуется (system-mcp-auto-auth.ts). efir требует
# OAuth, поэтому по умолчанию выключен — как в desktop у пользователя, который
# его не подключал.
DEFAULT_ENABLED = ("corpsearch",)

# Хвост MCP-endpoint'а корпоративного поиска (CORP_SEARCH_MCP_ENDPOINT в
# utils/env.ts). Хвост стабилен, origin приходит из конфига стенда.
CORP_SEARCH_MCP_ENDPOINT = "/app/se/rag-search/api/mcp/"


def system_mcp_path(repo_root: Path) -> Path:
    """Путь к ресурсу desktop-приложения с описанием системных MCP."""
    return Path(repo_root) / "packages" / "desktop-electron" / "resources" / "system-mcp.json"


def corp_search_mcp_url(shared_api_origin: str, corp_search_url: str = "") -> str:
    """URL corpsearch MCP: `<origin><base-path>/app/se/rag-search/api/mcp/`.

    Базовый путь (`/sberfriend-api/api/v1/ext`) берётся из уже собранного
    `CORP_SEARCH_URL` — так же, как desktop выводит оба адреса из одного
    `SHARED_API_ORIGIN` + `BASE_PATH`.
    """
    if corp_search_url and "/app/" in corp_search_url:
        base = corp_search_url.split("/app/", 1)[0]
    else:
        base = f"{shared_api_origin.rstrip('/')}/sberfriend-api/api/v1/ext"
    return base.rstrip("/") + CORP_SEARCH_MCP_ENDPOINT


def _substitute(raw: str, values: Mapping[str, str]) -> Optional[str]:
    """Подставить `${VAR}`; None — если хоть одна переменная неизвестна."""
    unknown = False

    def repl(match: re.Match[str]) -> str:
        nonlocal unknown
        name = match.group(1).strip()
        value = values.get(name)
        if not value:
            unknown = True
            return match.group(0)
        return value

    result = _ENV_PLACEHOLDER.sub(repl, raw)
    return None if unknown else result


def _enabled_names(value: Optional[str]) -> tuple:
    """`GIGATOOL_SYSTEM_MCP`: список имён через запятую; `none` — пусто."""
    if value is None:
        return DEFAULT_ENABLED
    text = value.strip()
    if not text:
        return DEFAULT_ENABLED
    if text.lower() in ("none", "off", "0"):
        return ()
    return tuple(name.strip() for name in text.split(",") if name.strip())


def load_system_mcp(
    path: Path,
    *,
    values: Optional[Mapping[str, str]] = None,
    enabled: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Прочитать ресурс и вернуть инъекцию `systemMcp` (пусто — нет серверов).

    `values` — подстановки для `${VAR}` (по умолчанию окружение процесса).
    `enabled` — какие серверы включены (по умолчанию `DEFAULT_ENABLED`).
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"[system-mcp] {path}: {err}", file=sys.stderr)
        return {}
    if not isinstance(parsed, dict):
        return {}

    subs: Dict[str, str] = dict(os.environ if values is None else values)
    on = set(DEFAULT_ENABLED if enabled is None else enabled)

    result: Dict[str, Any] = {}
    for name, entry in parsed.items():
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        url = entry.get("url")
        if isinstance(url, str) and "${" in url:
            resolved = _substitute(url, subs)
            if resolved is None:
                # Неподставленный плейсхолдер = нерабочий URL: сервер либо
                # повиснет в failed, либо подмешает в промт блок о недоступном
                # MCP. Лучше не инжектить его вовсе.
                print(f"[system-mcp] {name}: не удалось подставить url, сервер пропущен", file=sys.stderr)
                continue
            entry["url"] = resolved
        entry["system"] = True
        entry["enabled"] = name in on
        result[name] = entry
    return result


def injection_from_config(
    repo_root: Path,
    shared_api_origin: str,
    corp_search_url: str = "",
    enabled: Optional[str] = None,
) -> Dict[str, Any]:
    """Инъекция для текущего стенда: путь из ресурсов + посчитанный corpsearch URL."""
    values = dict(os.environ)
    values.setdefault("CORP_SEARCH_MCP_URL", corp_search_mcp_url(shared_api_origin, corp_search_url))
    values.setdefault("SHARED_API_ORIGIN", shared_api_origin.rstrip("/"))
    return load_system_mcp(
        system_mcp_path(repo_root),
        values=values,
        enabled=_enabled_names(enabled),
    )


__all__ = [
    "CORP_SEARCH_MCP_ENDPOINT",
    "DEFAULT_ENABLED",
    "corp_search_mcp_url",
    "injection_from_config",
    "load_system_mcp",
    "system_mcp_path",
]