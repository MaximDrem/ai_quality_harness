"""Обновление Sudir-куков (GIGACHAT_SESSION_COOKIES).

Куки живут ~13 часов, после чего upstream перестаёт отдавать список моделей
и агент падает с `Model not found: gigachat/...`. Здесь логинимся заново по
кредам из `.env` и пишем свежую Cookie-строку в окружение и в `.env`.

Импортирует `client.*` лениво: в чекауте без `client/` модуль недоступен,
и харнесс продолжает работать с прежними куками (например, `--validate`).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from ..config import TEST_AGENT_ROOT


def refresh_cookies() -> bool:
    """Перелогиниться в СУДИР и обновить GIGACHAT_SESSION_COOKIES.

    Возвращает True, если куки получены и записаны; при любой проблеме
    оставляем прежние.
    """
    try:
        from client.sudir_login import login, verify_session, write_cookies_env
        from client.config import ClientConfig
        import urllib.parse as _up
    except Exception as e:  # noqa: BLE001
        print(f"[cookies] модуль sudir_login недоступен: {e}", file=sys.stderr)
        return False

    username = os.environ.get("IFT_LOGIN_USER") or os.environ.get("IFT_LOGING")
    password = os.environ.get("IFT_PASSWORD")
    if not username or not password:
        print("[cookies] нет кред (IFT_LOGIN_USER/IFT_PASSWORD) — оставляю прежние куки", file=sys.stderr)
        return False

    # Логинимся в origin того upstream, куда пойдёт прокси: куки host-only,
    # сессия чужого стенда на API отдаёт 401 (выглядит как «Model not found»).
    try:
        u = _up.urlsplit(ClientConfig().resolve().gigachat_upstream)
        base = f"{u.scheme}://{u.netloc}"
    except Exception:  # noqa: BLE001
        base = None

    try:
        header = login(username, password, base_url=base)
    except Exception as e:  # noqa: BLE001
        print(f"[cookies] Sudir-логин не удался: {e} — оставляю прежние куки", file=sys.stderr)
        return False
    if not header:
        print("[cookies] логин не вернул куки — оставляю прежние", file=sys.stderr)
        return False

    st = verify_session(header, base_url=base)
    if st != 200:
        print(f"[cookies] session-check -> {st}, куки невалидны — оставляю прежние", file=sys.stderr)
        return False

    os.environ["GIGACHAT_SESSION_COOKIES"] = header
    # Пишем в `.env`, чтобы свежие куки видел и клиент вне пайплайна.
    try:
        write_cookies_env(str(TEST_AGENT_ROOT / ".env"), header)
    except Exception as e:  # noqa: BLE001
        print(f"[cookies] не удалось записать .env: {e}", file=sys.stderr)
    print("[cookies] Sudir-куки обновлены")
    return True