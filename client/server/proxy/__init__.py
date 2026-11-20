"""Cookie-прокси к GigaChat gateway (Python stdlib, локальный loopback).

Подпакет разбит по ответственности:

  * `models`   — отбор каталога моделей из ответа шлюза (без искажений);
  * `cookies`  — нормализация Cookie-строки;
  * `tracing`  — запись запроса/ответа upstream для raw.log;
  * `handler`  — HTTP-обработчик (форвард + `/models`);
  * `tee`      — прозрачный хоп перед mTLS-прокси (дамп/трейс в режиме `cert`);
  * `proxy`    — менеджер: банка кук, 401-интерсептор, жизненный цикл.

Публичные имена ре-экспортируются здесь, поэтому снаружи достаточно
`from client.server.proxy import GigachatProxy, normalize_cookies`.
"""
from __future__ import annotations

from .cookies import normalize_cookies
from .handler import GIGACHAT_USER_AGENT, GigaChatHandler
from .models import MODEL_SKIP_RE, select_gateway_models
from .proxy import (
    SESSION_EXPIRED_THROTTLE_MS,
    GigachatProxy,
    ThreadingHTTPServer,
    free_port,
    is_port_open,
)
from .tee import TeeProxy
from .tracing import apply_response_trace, build_request_trace

__all__ = [
    "GigachatProxy",
    "GigaChatHandler",
    "TeeProxy",
    "ThreadingHTTPServer",
    "SESSION_EXPIRED_THROTTLE_MS",
    "GIGACHAT_USER_AGENT",
    "MODEL_SKIP_RE",
    "select_gateway_models",
    "normalize_cookies",
    "build_request_trace",
    "apply_response_trace",
    "free_port",
    "is_port_open",
]