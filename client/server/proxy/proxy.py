"""Менеджер cookie-прокси к GigaChat (SberFriend gateway).

Новый backend (desktop-ai-app) больше НЕ использует mTLS-прокси: авторизация к
GigaChat теперь идёт через Sudir-сессионные куки, а `GIGACHAT_BASE_URL`
указывает прямо на SberFriend gateway. В десктопе куки инжектятся в sidecar
in-process через `setCustomFetch`; в headless (наш `gigatool serve`) их нет —
поэтому здесь поднимается лёгкий локальный прокси на Python (stdlib), который:

  * прикрепляет к каждому запросу заголовок `Cookie` (из `GIGACHAT_SESSION_COOKIES`)
    и WAF-friendly `User-Agent: GigaChat-GigaCat`;
  * срезает `Authorization` (Sber отклоняет mTLS-запросы с ним, как и cookie);
  * добавляет браузерные `Origin`/`Referer` (IAM-прокси platformV валидируют их
    и отвечают 400 клиенту, не похожему на браузер — см. apiClient.ts);
  * форвардит запросы на upstream SberFriend gateway (см. `GIGATOOL_GIGACHAT_UPSTREAM`);
  * стримит ответ поточно, вырезая hop-by-hop/фрейминг-заголовки ответа;
  * обновляет банку кук из `Set-Cookie` ответов upstream (как
    sessionService.applySetCookie) и зовёт слушателя;
  * на HTTP 401 от upstream сигнализирует «сессия истекла» слушателю с
    троттлингом 5 с (как apiClient.maybeNotifyExpired);
  * отдаёт `GET /models` в виде plain-массива `RawModel[]`, который парсит
    opencode sidecar (`packages/opencode/src/provider/models.ts`).

mTLS-сертификаты (gigakeys) больше не нужны.
"""
from __future__ import annotations

import http.server
import socket
import socketserver
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from ...core.config import ClientConfig
from ...core.errors import InstanceError
from .cookies import normalize_cookies
from .handler import GigaChatHandler
from .tracing import browser_origin_referer

# Троттлинг уведомления «сессия истекла» (как NOTIFY_THROTTLE_MS в apiClient.ts).
SESSION_EXPIRED_THROTTLE_MS = 5_000

# Сколько записей трейса держим в памяти как страховку/для отладки.
_TRACE_TAIL = 1000


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def free_port() -> int:
    """Найти свободный локальный порт (IPv4 loopback)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def is_port_open(port: int, timeout: float = 0.3) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class GigachatProxy:
    """Менеджер cookie-прокси: форвардит на SberFriend gateway с Sudir-куки."""

    def __init__(
        self,
        cfg: ClientConfig,
        port: Optional[int] = None,
        upstream: Optional[str] = None,
    ):
        self.cfg = cfg
        self.port = port or free_port()
        self.upstream = upstream or cfg.gigachat_upstream
        self.cookie_header: str = normalize_cookies("", cfg.session_cookies)
        self.upstream_timeout: float = float(getattr(cfg, "gigachat_upstream_timeout", 300.0))
        self.insecure_tls: bool = bool(getattr(cfg, "insecure_tls", True))
        self.base_url: Optional[str] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        # Банка кук / expired-состояние (как session-service в desktop).
        self._cookie_lock = threading.Lock()
        self._cookie_listener: Optional[Callable[[str], None]] = None
        self._session_expired_listener: Optional[Callable[[int, str], None]] = None
        self._expired_lock = threading.Lock()
        self._last_expired_notify = 0.0
        # Онлайн-трейс всех обращений к API LLM.
        #  * _sink — потокобезопасный callable(rec: dict), вызывается в момент
        #    обработки запроса (пишет в raw.log кейса онлайн).
        #  * _trace — in-memory «хвост» последних записей как страховка/отладка.
        self._sink: Optional[Callable[[Dict], None]] = None
        self._trace_lock = threading.Lock()
        self._trace: List[Dict] = []

    # ------------------------------------------------------------- listeners
    def attach_logger(self, sink: Callable[[Dict], None]) -> None:
        """Привязать online-sink, принимающий dict-трейс каждого API-запроса.

        `sink` — callable(rec: dict); вызывается в момент прохода трафика (не по
        завершении кейса). Для параллельных инстансов каждый прокси
        привязывается к своему raw.log кейса.
        """
        with self._trace_lock:
            self._sink = sink

    def attach_cookie_listener(self, listener: Optional[Callable[[str], None]] = None) -> None:
        """Слушатель обновления сессионных кук (Set-Cookie от upstream).

        `listener` — callable(cookie_header: str) — вызывается при каждом
        изменении банки кук (как sessionService.setCookies в desktop).
        """
        self._cookie_listener = listener

    def attach_session_expired_listener(
        self, listener: Optional[Callable[[int, str], None]] = None
    ) -> None:
        """Слушатель «Sudir-сессия истекла» (upstream вернул HTTP 401).

        Как apiClient.maybeNotifyExpired -> sessionService.notifyExpired:
        повторные 401 всплеска подавляются троттлингом 5 с. `listener` —
        callable(status: int, url: str).
        """
        self._session_expired_listener = listener

    # ------------------------------------------------------------ cookie jar
    def browser_origin_referer(self) -> Tuple[str, str]:
        """(Origin, Referer) от origin upstream — replay браузерных заголовков."""
        return browser_origin_referer(self.upstream)

    def _notify_cookie_listener(self) -> None:
        listener = self._cookie_listener
        if listener and self.cookie_header:
            try:
                listener(self.cookie_header)
            except Exception:  # noqa: BLE001 — слушатель не должен ломать прокси
                pass

    def apply_set_cookie(self, set_cookies: List[str]) -> Optional[str]:
        """Обновить банку кук из значений Set-Cookie upstream.

        Разбирает `name=value; Path=...; ...` и мерджит по имени в текущий
        Cookie-заголовок (как sessionService.applySetCookie). Возвращает
        обновлённый header-строку или None, если изменений нет.
        """
        if not set_cookies:
            return None
        with self._cookie_lock:
            jar: Dict[str, str] = {}
            order: List[str] = []
            for part in self.cookie_header.split(";"):
                name, _, value = part.strip().partition("=")
                if name:
                    jar[name] = value
                    order.append(name)
            changed = False
            for sc in set_cookies:
                first = sc.split(";", 1)[0].strip()
                name, _, value = first.partition("=")
                name = name.strip()
                if not name or not value:
                    continue
                if name not in jar:
                    order.append(name)
                jar[name] = value.strip()
                changed = True
            if not changed:
                return None
            self.cookie_header = "; ".join(f"{n}={jar[n]}" for n in order if n in jar)
        self._notify_cookie_listener()
        return self.cookie_header

    def set_cookie_header(self, header: str) -> None:
        """Полностью заменить Cookie-заголовок (после Sudir re-login).

        Как sessionService.setCookies в desktop после повторного логина:
        новые куки сразу используются всеми последующими запросами прокси.
        """
        with self._cookie_lock:
            self.cookie_header = (header or "").strip()
        self._notify_cookie_listener()

    def _notify_expired(self, url: str, status: int = 401) -> None:
        """401-интерсептор: сигнал «сессия истекла» с троттлингом 5 с
        (apiClient.maybeNotifyExpired). Факт пишется и в трейс."""
        now = time.time()
        with self._expired_lock:
            if now - self._last_expired_notify < SESSION_EXPIRED_THROTTLE_MS / 1000.0:
                return
            self._last_expired_notify = now
            listener = self._session_expired_listener
        self._trace_now({"ts": now, "session_expired": True, "status": status, "upstream": url})
        if listener:
            try:
                listener(status, url)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ trace
    def _trace_now(self, rec: Dict) -> None:
        """Пушить запись в sink и держать небольшой in-memory хвост."""
        with self._trace_lock:
            if self._sink:
                try:
                    self._sink(rec)
                except Exception:  # noqa: BLE001 — трейс не должен ломать прокси
                    pass
            self._trace.append(rec)
            if len(self._trace) > _TRACE_TAIL:
                self._trace = self._trace[-_TRACE_TAIL:]

    def drain_trace(self) -> List[Dict]:
        """Вернуть и очистить накопленный in-memory хвост записей."""
        with self._trace_lock:
            out, self._trace = self._trace, []
        return out

    # --------------------------------------------------------------- lifecycle
    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    def start(self, log_dir=None, wait_ready: bool = True) -> "GigachatProxy":
        """Запустить прокси (threading HTTPServer) на loopback-порту."""
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), GigaChatHandler)
        self._server._proxy = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self.port}"
        if wait_ready:
            self._wait_listening(self.cfg.health_timeout)
        return self

    def _wait_listening(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.running:
                raise InstanceError("proxy thread died")
            if is_port_open(self.port):
                return
            time.sleep(0.3)
        raise InstanceError(f"proxy did not start listening on port {self.port}")

    def stop(self, timeout: float = 10.0) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            finally:
                self._server.server_close()
        self._server = None
        self._thread = None

    def tail(self, n: int = 40) -> str:
        return f"<cookie-proxy on {self.port}>"


__all__ = [
    "GigachatProxy",
    "ThreadingHTTPServer",
    "SESSION_EXPIRED_THROTTLE_MS",
    "free_port",
    "is_port_open",
]