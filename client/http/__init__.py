"""Транспорт к headless-серверу opencode.

  * `HttpClient` — REST-запросы с Basic-auth и `x-opencode-directory`;
  * `EventStream` — фоновый SSE-подписчик на `GET /global/event`.
"""
from __future__ import annotations

from .client import HttpClient
from .event_stream import HEARTBEAT_TIMEOUT_MS, RECONNECT_DELAY_MS, EventStream

__all__ = ["HttpClient", "EventStream", "RECONNECT_DELAY_MS", "HEARTBEAT_TIMEOUT_MS"]