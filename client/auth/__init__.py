"""Аутентификация: Sudir-логин, проверка сессии и авто re-login.

Снаружи используется так же, как раньше `client.sudir_login`:

    from client.auth import sudir
    header = sudir.login(username, password, base_url=base)
    sudir.verify_session(header, base_url=base)

Для запуска из CLI: `python -m client.auth.sudir --verify`.
"""
from __future__ import annotations

from . import sudir
from .relogin import RELOGIN_COOLDOWN_S, SudirRelogin
from .sudir import (
    fetch_login_form,
    header_to_dict,
    login,
    verify_session,
    write_cookies_env,
)

__all__ = [
    "sudir",
    "SudirRelogin",
    "RELOGIN_COOLDOWN_S",
    "login",
    "verify_session",
    "fetch_login_form",
    "write_cookies_env",
    "header_to_dict",
]