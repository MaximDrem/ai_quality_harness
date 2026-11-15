"""Автоматический re-login Sudir-сессии (аналог desktop notifyExpired).

В desktop при 401 провайдерного запроса UI повторно логинится и подменяет куки
(`notifyExpired` -> `login` -> `setCookies`). Здесь то же самое для headless:
пре-флайт перед стартом инстанса и реакция на 401 от upstream.

Cooldown защищает от параллельных логинов из разных потоков прокси: прокси
обслуживает запросы в отдельных нитях, и каждый 401 хочет перелогиниться.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, Optional

from . import sudir

# Не чаще одного автоматического логина за это время (сек).
RELOGIN_COOLDOWN_S = 60.0


class SudirRelogin:
    """Перелогин Sudir-сессии.

    Для `ift`/`cert` креды берутся из IFT_LOGIN_USER/IFT_LOGING + IFT_PASSWORD
    (обычный парольный поток). Для `prod` парольной формы нет — вызывающая
    сторона передаёт `login_fn` (окно Electron, см. `auth.electron_login`),
    а логика cooldown/verify остаётся общей.
    """

    def __init__(
        self,
        base_url: str,
        enabled: bool = True,
        on_login: Optional[Callable[[str], None]] = None,
        cooldown: float = RELOGIN_COOLDOWN_S,
        login_fn: Optional[Callable[[], Optional[str]]] = None,
    ):
        self.base_url = base_url
        self.enabled = enabled
        self.on_login = on_login
        self.cooldown = cooldown
        self.login_fn = login_fn
        self._lock = threading.Lock()
        self._last_login = 0.0
        self.count = 0

    def verify(self, header: str) -> int:
        """GET /session-check с куками (200 = валидны). Ошибки сети -> 0."""
        try:
            return sudir.verify_session(header, base_url=self.base_url)
        except Exception:  # noqa: BLE001
            return 0

    def login(self, reason: str, force: bool = False) -> Optional[str]:
        """Выполнить re-login и вернуть свежий Cookie-заголовок.

        Возвращает None, если re-login отключён, креды не заданы, попытка
        уложилась в cooldown или session-check не подтвердил куки.
        """
        if not self.enabled:
            return None
        with self._lock:
            now = time.time()
            if not force and now - self._last_login < self.cooldown:
                return None
            self._last_login = now

        if self.login_fn is not None:
            # Внешний способ входа (prod: окно Electron + сертификат из ОС).
            try:
                header = self.login_fn()
            except Exception as e:  # noqa: BLE001
                print(f"[sudir] re-login не удался: {e}", file=sys.stderr)
                return None
            if not header:
                print("[sudir] re-login не вернул куки", file=sys.stderr)
                return None
        else:
            username = os.environ.get("IFT_LOGIN_USER") or os.environ.get("IFT_LOGING")
            password = os.environ.get("IFT_PASSWORD")
            if not username or not password:
                print(
                    "[sudir] нет кред IFT_LOGIN_USER/IFT_PASSWORD — авто re-login невозможен",
                    file=sys.stderr,
                )
                return None
            try:
                header = sudir.login(username, password, base_url=self.base_url)
            except Exception as e:  # noqa: BLE001
                print(f"[sudir] re-login не удался: {e}", file=sys.stderr)
                return None
            if not header:
                print("[sudir] re-login не вернул куки", file=sys.stderr)
                return None

        if self.verify(header) != 200:
            print("[sudir] re-login: session-check не 200 — куки не применены", file=sys.stderr)
            return None
        self.count += 1
        if self.on_login:
            try:
                self.on_login(header)
            except Exception:  # noqa: BLE001
                pass
        print(f"[sudir] re-login OK ({reason}) — куки обновлены")
        return header

    def ensure_fresh(self, stored_header: str) -> Optional[str]:
        """Пре-флайт: проверить куки и при необходимости перелогиниться.

        Без этого sidecar на старте получит 401 на GET /models, останется с
        пустым каталогом и каждое сообщение будет падать «Model not found: ...».
        """
        if not stored_header:
            return None
        if self.verify(stored_header) == 200:
            return stored_header
        print(
            "[sudir] session-check -> не 200: куки невалидны, пробую повторный логин",
            file=sys.stderr,
        )
        return self.login("preflight", force=True) or stored_header


__all__ = ["SudirRelogin", "RELOGIN_COOLDOWN_S"]