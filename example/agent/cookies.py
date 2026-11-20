"""Обновление IFT-кук Sudir.

Профиль, MCP и корп-поиск всегда берутся с ИФТ (dev-ветка desktop) при любом
`auth_mode` — поэтому здесь всегда парольный Sudir-логин на ИФТ. Прод-сессию
ШЛЮЗА моделей (`auth_mode=prod`) этот скрипт не трогает: её добывает клиент
через Electron (или она лежит в `GIGATOOL_PROD_SESSION_COOKIES`).
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from ..config import TEST_AGENT_ROOT


def refresh_cookies(auth_mode: Optional[str] = None) -> bool:
    mode = (auth_mode or os.environ.get("GIGATOOL_AUTH_MODE") or "ift").strip().lower()
    if mode == "prod":
        print(
            "[cookies] prod: прод-сессию шлюза добывает клиент через Electron; "
            "обновляю IFT-куки для профиля/MCP/поиска",
            file=sys.stderr,
        )
    try:
        from client import ClientConfig
        from client.auth.sudir import login, verify_session, write_cookies_env
    except Exception as error:
        print(f"[cookies] модуль Sudir недоступен: {error}", file=sys.stderr)
        return False

    username = os.environ.get("IFT_LOGIN_USER") or os.environ.get("IFT_LOGING")
    password = os.environ.get("IFT_PASSWORD")
    if not username or not password:
        print("[cookies] нет кред (IFT_LOGIN_USER/IFT_PASSWORD) — оставляю прежние куки", file=sys.stderr)
        return False

    try:
        # Профиль/MCP/поиск — всегда ИФТ, поэтому и стенд логина всегда ИФТ
        # (в `prod` это не требует GIGATOOL_PROD_SHARED_API_URL_ORIGIN).
        base = ClientConfig(auth_mode="ift").resolve().shared_api_origin
        header = login(username, password, base_url=base)
    except Exception as error:
        print(f"[cookies] Sudir-логин не удался: {error} — оставляю прежние куки", file=sys.stderr)
        return False
    if not header:
        print("[cookies] Sudir-логин не вернул куки — оставляю прежние", file=sys.stderr)
        return False
    status = verify_session(header, base_url=base)
    if status != 200:
        print(f"[cookies] session-check -> {status}, куки невалидны — оставляю прежние", file=sys.stderr)
        return False

    os.environ["GIGACHAT_SESSION_COOKIES"] = header
    try:
        write_cookies_env(str(TEST_AGENT_ROOT / ".env"), header)
    except Exception as error:
        print(f"[cookies] не удалось записать .env: {error}", file=sys.stderr)
    print("[cookies] Sudir-куки обновлены")
    return True
