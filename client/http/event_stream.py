"""SSE-клиент события сервера (`GET /global/event`).

Повторяет поведение desktop-клиента (packages/app/src/context/global-sdk.tsx
+ packages/sdk/js/src/gen/core/serverSentEvents.gen.ts):

  * вечный цикл подписки: обрыв соединения -> пауза RECONNECT_DELAY_MS (250 мс)
    -> новое подключение (в desktop так же: `await wait(RECONNECT_DELAY_MS)`);
  * heartbeat-watchdog: сервер шлёт `server.heartbeat` каждые 10 с; если
    HEARTBEAT_TIMEOUT_MS (15 с) ни одного кадра — соединение разрывается и
    переподключается (в desktop: `attempt?.abort()` по таймеру бездействия);
  * поля кадра `data:` / `event:` / `id:` / `retry:`; полученный `id`
    возвращается серверу заголовком `Last-Event-ID` при переподключении
    (как hey-api SSE-клиент);
  * JSON-парсинг `data:` с фолбэком на строку;
  * глобальный канал оборачивает событие в
    `{directory, project, payload: {id, type, properties}}` — payload
    разворачивается наверх (как это видит desktop-клиент).

Реализация — только стандартная библиотека (urllib), фоновый поток.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

# Константы desktop-клиента (global-sdk.tsx).
RECONNECT_DELAY_MS = 250
HEARTBEAT_TIMEOUT_MS = 15_000  # сервер шлёт heartbeat каждые 10 с
LISTEN_CONNECT_TIMEOUT_S = 10.0  # таймаут на установку соединения

OnEvent = Callable[[Dict[str, Any]], None]
OnStatus = Callable[[str, Dict[str, Any]], None]  # (state, info): open|reconnect|error|closed


class EventStream:
    """Фоновый подписчик на `GET /global/event` с автопереподключением."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        directory: str,
        on_event: Optional[OnEvent] = None,
        on_status: Optional[OnStatus] = None,
        heartbeat_timeout: float = HEARTBEAT_TIMEOUT_MS / 1000.0,
        reconnect_delay: float = RECONNECT_DELAY_MS / 1000.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.directory = directory
        self.on_event = on_event
        self.on_status = on_status
        # Watchdog бездействия: если сервер молчит дольше heartbeat_timeout —
        # соединение считается мёртвым (heartbeat должен приходить каждые 10 с).
        self.heartbeat_timeout = heartbeat_timeout
        self.reconnect_delay = reconnect_delay
        self._auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._open = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._resp = None
        # Последний полученный event-id (для Last-Event-ID при реконнекте,
        # как в hey-api serverSentEvents.gen.ts).
        self._last_event_id: Optional[str] = None
        self._last_error: Optional[str] = None
        # Счётчики для наблюдаемости.
        self.stats: Dict[str, int] = {"connections": 0, "reconnects": 0, "events": 0, "errors": 0}

    # ------------------------------------------------------------- control
    def start(self) -> "EventStream":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="opencode-sse")
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._close_resp()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    @property
    def is_open(self) -> bool:
        return self._open and not self._stop.is_set()

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def wait_open(self, timeout: float = 10.0) -> bool:
        """Дождаться установленного SSE-соединения (первый кадр server.connected)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop.is_set():
                return False
            if self._open:
                return True
            time.sleep(0.05)
        return False

    # ---------------------------------------------------------------- loop
    def _run(self) -> None:
        """Вечный цикл подписки — как `start()` в global-sdk.tsx:

            while (!aborted) {
                events = await sdk.global.event({signal})
                for await (event of events.stream) { resetHeartbeat(); ... }
                await wait(RECONNECT_DELAY_MS)   // и заново
        """
        while not self._stop.is_set():
            try:
                self._connect_once()
            except _AuthError as e:
                # 401/403: переподключаться бесполезно — пароль сервера не меняется,
                # фиксируем и выходим (слушатель остановлен).
                self._last_error = str(e)
                self.stats["errors"] += 1
                self._emit_status("error", {"error": str(e), "fatal": True})
                self._open = False
                return
            except Exception as e:  # noqa: BLE001
                self._last_error = repr(e)
                self.stats["errors"] += 1
                self._emit_status("error", {"error": repr(e)})
            finally:
                self._open = False
                self._close_resp()

            if self._stop.is_set():
                break
            # После любого обрыва — пауза RECONNECT_DELAY_MS и новое
            # подключение (как `await wait(RECONNECT_DELAY_MS)` в desktop).
            self.stats["reconnects"] += 1
            self._emit_status("reconnect", {"delay_ms": int(self.reconnect_delay * 1000)})
            self._stop.wait(self.reconnect_delay)

        self._emit_status("closed", {})

    def _connect_once(self) -> None:
        req = urllib.request.Request(
            f"{self.base_url}/global/event",
            headers=self._headers(),
        )
        self._emit_status("connecting", {"last_event_id": self._last_event_id})
        try:
            resp = urllib.request.urlopen(req, timeout=self.heartbeat_timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                e.close()
                raise _AuthError(f"SSE connect -> HTTP {e.code}") from e
            raise
        self._resp = resp
        self.stats["connections"] += 1
        self._open = True
        self._emit_status("open", {})
        self._read_stream(resp)

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Basic {self._auth}",
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        if self._last_event_id:
            headers["Last-Event-ID"] = self._last_event_id
        return headers

    def _read_stream(self, resp) -> None:
        """Парсинг SSE-кадров. Блоки разделяются пустой строкой; внутри —
        строки `data:` / `event:` / `id:` / `retry:` (как hey-api парсер)."""
        event_name = "message"
        data_lines: List[str] = []
        for raw in resp:
            if self._stop.is_set():
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                # Конец блока события.
                if data_lines:
                    self._dispatch_event(event_name, "\n".join(data_lines))
                data_lines = []
                event_name = "message"
                continue
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip())
            elif line.startswith("id:"):
                self._last_event_id = line[len("id:"):].strip()
            elif line.startswith("retry:"):
                # Сервер может менять интервал реконнекта (hey-api SSE).
                try:
                    self.reconnect_delay = max(0.05, int(line[len("retry:"):].strip()) / 1000.0)
                except ValueError:
                    pass
            # Комментарии (`: keep-alive`) и прочие поля игнорируются.

    def _dispatch_event(self, event_name: str, data: str) -> None:
        self.stats["events"] += 1
        try:
            obj = json.loads(data)
        except (ValueError, TypeError):
            obj = {"data": data}
        if not isinstance(obj, dict):
            obj = {"data": data}
        # Глобальный канал оборачивает событие в {directory, project, payload}.
        # Разворачиваем payload наверх — как desktop-клиент (global-sdk.tsx:
        # `const directory = event.directory ?? "global"; ... event.payload`).
        if isinstance(obj.get("payload"), dict):
            payload = obj["payload"]
            payload.setdefault("_event", event_name)
            payload.setdefault("_directory", obj.get("directory"))
            obj = payload
        else:
            obj.setdefault("_event", event_name)
        if self.on_event:
            try:
                self.on_event(obj)
            except Exception:  # noqa: BLE001 — колбэк не должен ронять слушателя
                pass

    # -------------------------------------------------------------- helpers
    def _close_resp(self) -> None:
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

    def _emit_status(self, state: str, info: Dict[str, Any]) -> None:
        if self.on_status:
            try:
                self.on_status(state, info)
            except Exception:  # noqa: BLE001
                pass


class _AuthError(Exception):
    """Неустранимая ошибка аутентификации SSE (HTTP 401/403)."""


__all__ = ["EventStream", "RECONNECT_DELAY_MS", "HEARTBEAT_TIMEOUT_MS"]