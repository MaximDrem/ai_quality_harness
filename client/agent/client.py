"""Высокоуровневый клиент для работы с одним headless-инстансом opencode.

Интерфейс:
  * `create_session()`                      — создать сессию;
  * `listen()` / `stop_listening()`         — фоновый SSE-слушатель /global/event,
  * `send_prompt(...)`                      — отправить сообщение и дождаться,
                                              пока агент не завершит (idle);
  * `agent_actions(...)`                    — итерироваться по действиям агента
                                              (потоковый/пошаговый интерфейс);
  * `abort(...)`                            — прервать генерацию сессии;
  * `messages(...)` / `message(...)`        — история сообщений сессии;
  * `extract_artifacts(...)`                — найти пути к созданным файлам.

Реализация повторяет desktop-клиент (packages/app): подписка на /global/event
(SSE с реконнектом 250 мс и heartbeat-watchdog 15 с), генерация messageID на
клиенте (`Identifier.ascending("message")`), отправка промпта через
`/session/:id/prompt_async` (204, результат приходит только событиями) и
retry-aware ожидание: сервер сам ретраит сбои генерации (до 8 попыток с
backoff 2с→30с, session/retry.ts), при исчерпании публикует session.error и
переводит сессию в idle — клиент при retry не отваливается, а ждёт итог.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from ..core import ids
from ..core.errors import AgentError, SessionError
from ..core.models import AgentEvent, Artifact
from ..http.client import HttpClient
from ..http.event_stream import EventStream
from .events import extract_error, parse_event
from .result import AgentResult, build_result

EventHandler = Callable[[AgentEvent], None]

# Типы событий, означающие активность генерации сессии (продлевают окно
# ожидания, как heartbeat-watchdog в desktop, но для «живой» сессии).
_ACTIVE_EVENT_PREFIXES = ("session.", "message.", "permission.", "question.")

# Пауза опроса событий в ожидании idle (сек).
_POLL_INTERVAL = 0.2
_ACTIONS_POLL_INTERVAL = 0.1
# Сколько ждать установленного SSE-соединения при старте слушателя.
_LISTENER_READY_TIMEOUT = 15.0
# Провайдер моделей, если имя передано без `provider/` (sidecar обёрнут
# GigaChat-прокси, других провайдеров в /config/providers нет).
_DEFAULT_PROVIDER_ID = "gigachat"


def parse_model_ref(model: Any, providers: Optional[Dict[str, Any]] = None) -> Any:
    """Нормализовать ссылку на модель в объект {providerID, modelID}.

    Сервер ожидает именно объект (как это делает `gigatool run` /
    `Provider.parseModel`): голая строка без `provider/` отклоняется с
    HTTP 400 (`Expected object | null, got "..."` на `model`). Поэтому имя
    без провайдера разворачивается в полную ссылку: провайдер ищется по
    `providers` (providerID -> {modelID: …}), иначе берётся `gigachat`.
    Уже объект — пропускаем как есть.
    """
    if isinstance(model, dict):
        return model
    if isinstance(model, str) and model:
        if "/" in model:
            provider, _, mid = model.partition("/")
            return {"providerID": provider, "modelID": mid}
        for provider_id, models in (providers or {}).items():
            if model in models:
                return {"providerID": provider_id, "modelID": model}
        return {"providerID": _DEFAULT_PROVIDER_ID, "modelID": model}
    return model


class AgentClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        work_dir: str,
        model: str = "gigachat/GigaChat-3-Ultra",
        agent: str = "build",
        event_idle_timeout: float = 900.0,
        event_buffer_size: int = 100_000,
    ):
        self.base_url = base_url
        self.work_dir = work_dir
        self.model = model
        self.agent = agent
        self.event_idle_timeout = event_idle_timeout
        self.http = HttpClient(base_url, username, password, work_dir)
        self._listener: Optional[EventStream] = None
        self._thread: Optional[threading.Thread] = None
        self._listener_ready = threading.Event()
        self._events: List[AgentEvent] = []
        self._lock = threading.Lock()
        self._handlers: List[EventHandler] = []
        self._stop = threading.Event()
        self._provider_models: Optional[Dict[str, List[str]]] = None

    # ------------------------------------------------------------- события
    def on_event(self, handler: EventHandler) -> None:
        """Зарегистрировать колбэк на каждое нормализованное событие."""
        self._handlers.append(handler)

    def start_listening(self) -> None:
        if self._listener is not None and self._listener.is_open:
            return
        self._stop.clear()
        self._listener_ready.clear()
        if not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(target=self._run_listener, daemon=True)
            self._thread.start()
        # Дожидаемся установленного SSE-соединения (первый кадр
        # server.connected), чтобы не потерять события, возникающие сразу
        # после отправки промпта.
        self._listener_ready.wait(timeout=_LISTENER_READY_TIMEOUT)

    def _run_listener(self) -> None:
        def on_raw(obj: Dict[str, Any]) -> None:
            event = parse_event(obj)
            # Глобальный канал дублирует каждое событие служебным 'sync' —
            # не включаем его в результирующий поток.
            if event.type == "sync":
                return
            with self._lock:
                self._events.append(event)
            for h in self._handlers:
                try:
                    h(event)
                except Exception:
                    # Колбэк не должен ронять слушателя.
                    pass

        def on_status(state: str, info: Dict[str, Any]) -> None:
            if state == "open":
                self._listener_ready.set()
            elif state == "error":
                # Реконнект выполняет сам EventStream (как global-sdk.tsx);
                # факт ошибки фиксируем в потоке событий для наблюдаемости.
                with self._lock:
                    self._events.append(
                        AgentEvent(type="listener.error", properties=info)
                    )

        self._listener = EventStream(
            self.base_url,
            self.http.username,
            self.http.password,
            self.work_dir,
            on_event=on_raw,
            on_status=on_status,
        )
        self._listener.start()
        # Блокируемся до остановки (EventStream держит собственный вечный
        # цикл переподключения).
        self._stop.wait()

    def stop_listening(self) -> None:
        self._stop.set()
        self._listener_ready.set()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def snapshot_events(self) -> List[AgentEvent]:
        with self._lock:
            return list(self._events)

    def clear_events(self) -> None:
        with self._lock:
            self._events.clear()

    # ----------------------------------------------------------- сессии
    def create_session(self, title: Optional[str] = None) -> str:
        payload: Dict[str, Any] = {}
        if title:
            payload["title"] = title
        data = self.http.post("/session", payload)
        if isinstance(data, dict):
            sid = data.get("id") or data.get("sessionID")
            if sid:
                return sid
        # На некоторых бэкендах создание возвращает 204; пробуем получить список.
        sessions = self.http.get("/session", params={"limit": 1})
        if isinstance(sessions, list) and sessions:
            sid = sessions[0].get("id") or sessions[0].get("sessionID")
            if sid:
                return sid
        raise SessionError(f"could not create session, response={data!r}")

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.http.get("/session", params={"limit": limit}) or []

    def get_session(self, session_id: str) -> Dict[str, Any]:
        return self.http.get(f"/session/{session_id}")

    def provider_models(self) -> Dict[str, List[str]]:
        """providerID -> список modelID из `/config/providers` (кэшируется)."""
        if self._provider_models is None:
            data = self.http.get("/config/providers") or {}
            self._provider_models = {
                provider["id"]: list(provider.get("models") or {})
                for provider in data.get("providers") or []
                if provider.get("id")
            }
        return self._provider_models

    def _model_ref(self, model: Any) -> Any:
        """Ссылка на модель для prompt_async; голая строка -> объект."""
        if isinstance(model, str) and model and "/" not in model:
            return parse_model_ref(model, self.provider_models())
        return parse_model_ref(model)

    # ----------------------------------------------------------- промпты
    def _build_prompt_payload(
        self,
        message: str,
        agent: Optional[str],
        model: Optional[str],
        variant: Optional[str] = None,
        extra_parts: Optional[List[Dict[str, Any]]] = None,
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        parts = [{"type": "text", "text": message}]
        if extra_parts:
            parts.extend(extra_parts)
        payload: Dict[str, Any] = {
            "agent": agent or self.agent,
            "messageID": message_id or ids.ascending("msg"),
            "parts": parts,
        }
        if model:
            payload["model"] = self._model_ref(model)
        if variant:
            payload["variant"] = variant
        return payload

    def _attachment_parts(
        self,
        message: str,
        attachments: List[str],
        session_id: str,
        message_id: str,
        agent: Optional[str],
        model: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Части сообщения с вложениями — так же, как их строит UI desktop.

        Файлы задаются путями: `prepare_attachments` повторяет клиентскую
        подготовку UI (pptx — ссылкой на файл, xlsx/xls — CSV-«пилюля»,
        остальное — data-URL), а `build_message_parts` раскладывает части в
        том же порядке (текст, файлы, синтетические блоки, манифест
        `[Приложенные файлы: …]` в конце).

        Материализацию вложения выполнит сам сервер, когда получит промпт:
        копирование в `uploads/`, извлечение Office-текста и header
        `[Attached file: …]` — его работа (`SessionPrompt.resolvePart`).
        Прогонять части через мост здесь нельзя: он вызывает тот же
        `SessionPrompt` и вернул бы уже развёрнутые сервером части, которые
        затем обработались бы второй раз — header попадал бы в запрос к
        модели дважды.
        """
        from ..attachments import bridge

        prepared = bridge.prepare_attachments(attachments, base_dir=Path(self.work_dir))
        return bridge.build_message_parts(message, prepared)

    def send_prompt(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        agent: Optional[str] = None,
        variant: Optional[str] = None,
        dangerously_skip_permissions: bool = False,
        extra_parts: Optional[List[Dict[str, Any]]] = None,
        on_event: Optional[EventHandler] = None,
        attachments: Optional[List[str]] = None,
    ) -> AgentResult:
        """Отправить сообщение и дождаться завершения агента (idle).

        Повторяет desktop-поток (packages/app/src/components/prompt-input/
        submit.ts): клиент сам генерирует messageID (ULID), создаёт сессию и
        шлёт `prompt_async` (204, fire-and-forget) — результат приходит
        исключительно событиями по SSE.

        `attachments` — пути к файлам, которые надо приложить к сообщению.
        Их подготовка и материализация идут через модули desktop-ai-app
        (`client.attachments`), поэтому модель получает те же части, что и из UI.

        Сбои генерации обрабатывает сервер (retry-политика: до 8 попыток,
        задержки 2с→30с, уважение retry-after; после исчерпания —
        session.error и idle). Клиент ждёт idle несмотря на retry-статусы.
        """
        sid = session_id or self.create_session()
        payload = self._build_prompt_payload(message, agent, model, variant, extra_parts)
        message_id = payload["messageID"]
        if attachments:
            payload["parts"] = self._attachment_parts(message, attachments, sid, message_id, agent, model)
        if on_event:
            self.on_event(on_event)

        self.start_listening()
        self.clear_events()
        self._send_prompt_async(sid, payload)

        events = self._wait_for_idle(sid, auto_approve=dangerously_skip_permissions)
        return build_result(sid, events, message_id=message_id)

    def _send_prompt_async(self, session_id: str, payload: Dict[str, Any]) -> None:
        self.http.post(f"/session/{session_id}/prompt_async", payload)

    def abort(self, session_id: str) -> None:
        """Прервать генерацию сессии (как `session.abort` в desktop UI)."""
        self.http.post(f"/session/{session_id}/abort", {})

    # ---------------------------------------------------------- история
    def messages(self, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """История сообщений сессии (GET /session/:id/message)."""
        params: Dict[str, Any] = {}
        if limit:
            params["limit"] = limit
        data = self.http.get(f"/session/{session_id}/message", params=params)
        return data if isinstance(data, list) else []

    def message(self, session_id: str, message_id: str) -> Dict[str, Any]:
        """Одно сообщение сессии (GET /session/:id/message/:mid)."""
        return self.http.get(f"/session/{session_id}/message/{message_id}") or {}

    # ---------------------------------------------------------- ожидание
    def _abort_quietly(self, session_id: str) -> None:
        try:
            self.abort(session_id)
        except SessionError:
            pass

    def _wait_for_idle(self, session_id: str, auto_approve: bool = False) -> List[AgentEvent]:
        """Ждать idle сессии, не отваливаясь на retry-статусах.

        Сервер opencode при сбое генерации сам ретраит провайдера
        (session/retry.ts): публикует session.status {type:"retry", attempt,
        message, next} и перезапускает стрим — генерация ПРОДОЛЖАЕТСЯ. Клиент
        поэтому (как retry-баннер в desktop UI):

          * на session.status retry — продолжает ждать и продлевает окно
            ожидания (это активность сессии, а не зависание);
          * на session.error — фиксирует структурированную ошибку, но ждёт
            idle (Runner ставит idle один раз после halt);
          * idle — единственный успешный исход.

        Зависание определяется по отсутствию событий сессии дольше
        `event_idle_timeout`: как desktop UI — сессия абортится
        (POST /session/:id/abort) и поднимается ошибка.
        """
        index = 0
        last_activity = time.time()
        last_error: Optional[str] = None
        while True:
            events = self.snapshot_events()
            while index < len(events):
                ev = events[index]
                index += 1
                if ev.session_id and ev.session_id != session_id:
                    continue
                if ev.type.startswith(_ACTIVE_EVENT_PREFIXES):
                    last_activity = time.time()
                if ev.type == "session.error" and ev.session_id == session_id:
                    last_error = extract_error(ev)
                if ev.type == "permission.asked" and ev.session_id == session_id:
                    if auto_approve:
                        self._approve_permission(session_id, ev)
                if ev.is_idle and ev.session_id == session_id:
                    return self.snapshot_events()
            if time.time() - last_activity > self.event_idle_timeout:
                # Сессия молчит дольше лимита — прерываем генерацию (как
                # desktop UI abort) и выходим с ошибкой.
                self._abort_quietly(session_id)
                if last_error:
                    raise AgentError(last_error)
                raise SessionError(
                    f"timed out waiting for session activity "
                    f"(>{self.event_idle_timeout}s); session aborted"
                )
            time.sleep(_POLL_INTERVAL)

    def _approve_permission(self, session_id: str, ev: AgentEvent) -> None:
        """Ответить на permission request:
        POST /permission/{requestID}/reply с телом {"reply": "once"}.
        requestID берётся из события permission.asked (properties.id).
        """
        req_id = ev.properties.get("id") or ev.properties.get("requestID")
        if not req_id:
            return
        try:
            self.http.post(f"/permission/{req_id}/reply", {"reply": "once"})
        except SessionError as e:
            # Не молчим: неотвеченный permission подвешивает сессию до
            # event_idle_timeout, причина должна быть видна в логе.
            print(f"[permission] reply failed for {req_id}: {e}", file=sys.stderr)

    # --------------------------------------------------------- результат
    def extract_artifacts(self, result: AgentResult) -> List[Artifact]:
        """Повторно извлечь артефакты из результата (уже есть в result.artifacts)."""
        return result.artifacts

    # -------------------------------------------------- потоковый/пошаговый
    def agent_actions(
        self,
        message: str,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        agent: Optional[str] = None,
        on_event: Optional[EventHandler] = None,
        dangerously_skip_permissions: bool = False,
        attachments: Optional[List[str]] = None,
    ) -> Iterator[AgentEvent]:
        """Итератор действий агента. Yields события по мере поступления.

        `attachments` — пути к файлам, которые надо приложить (готовятся так
        же, как в UI: см. `send_prompt`).

        Завершается, когда сессия переходит в idle. Retry-статусы
        (session.status type=retry) пробрасываются наверх как обычные события,
        но НЕ завершают итерацию — сервер продолжает генерацию после ретрая.
        Отказ сервера принять промпт (HTTP 4xx) поднимается сразу: иначе
        генерация не стартует, а итерация ждёт idle до таймаута простоя.
        При зависании сессии (нет событий сессии дольше event_idle_timeout)
        сессия абортится и поднимается ошибка.
        """
        sid = session_id or self.create_session()
        if on_event:
            self.on_event(on_event)
        self.start_listening()
        self.clear_events()

        payload = self._build_prompt_payload(message, agent, model)
        if attachments:
            payload["parts"] = self._attachment_parts(
                message, attachments, sid, payload["messageID"], agent, model
            )
        last_index = 0
        prompt_error: List[SessionError] = []

        def pump() -> None:
            try:
                self._send_prompt_async(sid, payload)
            except SessionError as e:
                prompt_error.append(e)
                # Было: сбой отправки отдавали событием listener.error, а
                # ожидание продолжалось — прогон висел на heartbeat'ах до
                # event_idle_timeout, хотя генерация так и не стартовала.
                # with self._lock:
                #     self._events.append(AgentEvent(type="listener.error", properties={"error": str(e)}))

        threading.Thread(target=pump, daemon=True).start()

        last_activity = time.time()
        while True:
            # Сервер не принял промпт (напр. HTTP 400 из-за неверного model) —
            # поднимаем ошибку сразу, а не ждём idle до таймаута простоя.
            if prompt_error:
                raise prompt_error[0]
            events = self.snapshot_events()
            while last_index < len(events):
                ev = events[last_index]
                last_index += 1
                if ev.session_id and ev.session_id != sid:
                    continue
                if ev.type.startswith(_ACTIVE_EVENT_PREFIXES):
                    last_activity = time.time()
                yield ev
                if ev.type == "permission.asked" and dangerously_skip_permissions:
                    self._approve_permission(ev.session_id or sid, ev)
                if ev.is_idle and ev.session_id == sid:
                    return
            if time.time() - last_activity > self.event_idle_timeout:
                self._abort_quietly(sid)
                raise SessionError(
                    f"agent_actions timed out (>{self.event_idle_timeout}s); session aborted"
                )
            time.sleep(_ACTIONS_POLL_INTERVAL)


__all__ = ["AgentClient", "AgentResult", "parse_model_ref", "parse_event"]