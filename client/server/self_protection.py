"""Значение `OPENCODE_SELF_PROTECTION` — как `selfProtectionValue()` в desktop.

Sidecar включает блок `<self-protection>` в системный промт ровно тогда, когда
в этом JSON есть хотя бы один защищённый путь (`SelfProtection.enabled()`), а
сами пути ещё и блокируют tool-вызовы внутрь приложения и его runtime-данных.
Headless-обвязке нужен тот же блок, иначе промт расходится с desktop.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

# Runtime-каталоги приложения (`Global.Path` в sidecar'е).
_DATA_SUBDIRS = ("plans", "tool-output", "uploads", "worktree")

# `APP_NAMES` в initAppIdentity.ts: dev и beta живут отдельно от prod.
_CHANNEL_SUFFIX = {"dev": ".dev", "beta": ".beta", "prod": ""}


def base_dir() -> Path:
    """`Global.Path.base`: `OPENCODE_BASE_DIR` либо `~/.wsc`."""
    override = os.environ.get("OPENCODE_BASE_DIR", "").strip()
    return Path(override) if override else Path.home() / ".wsc"


def app_data_dir() -> Path:
    """`app.getPath("appData")` — корень, где Electron держит userData."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def user_data_dir(channel: str = "dev") -> Path:
    """`app.getPath("userData")` после `setPath` в initAppIdentity.ts."""
    return app_data_dir() / ("wsc" + _CHANNEL_SUFFIX.get(channel, ".dev"))


def self_protection(
    app_path: Path,
    *,
    base: Path | None = None,
    config_dir: Path | None = None,
    channel: str = "dev",
    packaged: bool = False,
) -> Dict[str, Any]:
    """Набор защищённых/разрешённых/только-читаемых путей приложения."""
    base = base or base_dir()
    config = config_dir or base / "config"
    app_path = Path(app_path)
    resources = app_path / "resources"
    protected = [app_path, resources, user_data_dir(channel), base]
    allowed = [
        # Как в desktop: непакованная сборка оставляет appPath доступным.
        *(() if packaged else (app_path,)),
        config / "skills",
        *(base / "data" / name for name in _DATA_SUBDIRS),
    ]
    return {
        "protected": _unique(protected),
        "allowed": _unique(allowed),
        "readOnly": [base / "data" / "log"],
    }


def self_protection_value(
    app_path: Path,
    *,
    base: Path | None = None,
    config_dir: Path | None = None,
    channel: str = "dev",
    packaged: bool = False,
) -> str:
    """JSON-строка для env `OPENCODE_SELF_PROTECTION`."""
    raw = self_protection(
        app_path, base=base, config_dir=config_dir, channel=channel, packaged=packaged
    )
    return json.dumps({key: [str(path) for path in value] for key, value in raw.items()}, ensure_ascii=False)


def _unique(paths) -> list:
    """Дедуп с сохранением порядка (как `[...new Set(...)]` в desktop)."""
    seen = set()
    result = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


__all__ = [
    "app_data_dir",
    "base_dir",
    "self_protection",
    "self_protection_value",
    "user_data_dir",
]