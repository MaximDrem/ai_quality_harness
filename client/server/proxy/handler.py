"""HTTP-обработчик cookie-прокси: `GET /models` и слепой форвард на upstream.

Один обработчик обслуживает одно соединение (threading-сервер поднимает
отдельный поток на соединение). Ответ проксируется **поточно** (chunked), чтобы
работал SSE-стриминг chat/completions; параллельно копится трейс запроса/ответа
и пушится в raw.log кейса онлайн (в момент прохода трафика).

Обработчик знает о состоянии прокси (`cookie_header`, `upstream_timeout`,
слушатель expired-сессии, sink трейса) только через объект `GigachatProxy`.
"""
from __future__ import annotations

import http.server
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Dict, List, Optional

from .models import select_gateway_models
from .tracing import (
    RESP_SNIPPET_BYTES,
    apply_response_trace,
    build_request_trace,
    finish_trace,
)

if TYPE_CHECKING:  # pragma: no cover — только для аннотаций
    from .proxy import GigachatProxy

# WAF-friendly User-Agent, как у desktop (packages/opencode/.../constants.ts).
GIGACHAT_USER_AGENT = "GigaChat-GigaCat"

# Hop-by-hop / framing headers запроса, которые нельзя ретранслировать.
STRIP_HEADERS = {
    "host",
    "authorization",
    "cookie",
    "user-agent",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "accept-encoding",
}

# Hop-by-hop / framing заголовки ОТВЕТА, вырезаемые при пересылке.
RESPONSE_STRIP_HEADERS = {
    "content-encoding",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "content-type",
}


def _ssl_context(insecure: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _header(headers: Dict[str, str], name: str, default: Optional[str] = None) -> Optional[str]:
    """Регистронезависимый доступ к заголовкам запроса.

    `dict(self.headers.items())` сохраняет регистр, в котором заголовок пришёл
    на провод, а HTTP-имена регистронезависимы: клиент (undici в sidecar'е)
    может прислать `content-length`, и поиск по `Content-Length` его не найдёт.
    """
    lname = name.lower()
    for k, v in headers.items():
        if k.lower() == lname:
            return v
    return default


class GigaChatHandler(http.server.BaseHTTPRequestHandler):
    """Прокси-обработчик: /models и слепой форвард на upstream."""

    protocol_version = "HTTP/1.1"

    @property
    def _proxy(self) -> "GigachatProxy":
        return self.server._proxy  # type: ignore[attr-defined]

    # ---------------------------------------------------------------- helpers
    def _respond(
        self,
        status: int,
        body: bytes,
        content_type: str = "application/json",
        extra: Optional[dict] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        # Безопасное завершение сессии: всегда закрываем соединение после
        # ответа. Это прокси-тонкий слой, которого не нужен keep-alive; частая
        # причина ConnectionResetError — клиент (opencode) сам закрывает
        # idle keep-alive сокет ровно в момент, когда мы читаем следующий
        # запрос (readline в handle_one_request). Принудительный `Connection:
        # close` заставляет нас закрыть сокет штатно (FIN, не RST).
        self.send_header("Connection", "close")
        self.close_connection = True
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Клиент (opencode) оборвал соединение до того, как мы отдали
            # ответ — типично при таймауте апстрима: сторож клиента срабатывает
            # раньше, чем прокси успевает прислать 502. Не даём исключению
            # уйти в socketserver (иначе лог засоряется ConnectionResetError и
            # трейс ответа теряется). close_connection уже выставлен выше.
            pass

    def _trace_end(self, rec: Dict, t0: float) -> None:
        self._proxy._trace_now(finish_trace(rec, t0))

    def _forward_headers(
        self, request_headers: Dict[str, str], body: Optional[bytes]
    ) -> Dict[str, str]:
        """Заголовки запроса к upstream: наши + клиентские, без hop-by-hop."""
        hdrs: Dict[str, str] = {
            "User-Agent": GIGACHAT_USER_AGENT,
            "Accept": _header(request_headers, "Accept", "*/*") or "*/*",
        }
        cookie = self._proxy.cookie_header
        if cookie:
            hdrs["Cookie"] = cookie
        # Браузерные заголовки (как apiFetch replay в desktop apiClient.ts:
        # "Some IAM proxies (platformV psi) validate e.g. User-Agent/Origin
        # and answer 400 to a client that doesn't look like the browser").
        origin, referer = self._proxy.browser_origin_referer()
        if origin:
            hdrs.setdefault("Origin", origin)
        if referer:
            hdrs.setdefault("Referer", referer)
        for k, v in request_headers.items():
            if k.lower() in STRIP_HEADERS:
                continue
            hdrs[k] = v
        content_type = _header(request_headers, "Content-Type")
        if body is not None and content_type:
            hdrs["Content-Type"] = content_type
        return hdrs

    def _read_chunked_body(self) -> Optional[bytes]:
        """Прочитать тело запроса с `Transfer-Encoding: chunked`.

        Sidecar (undici) для POST /chat/completions отдаёт тело чанками.
        Без этой ветки `Content-Length` в исходном запросе отсутствует, тело
        терялось и в upstream уходил пустой POST — прод-шлюз отвечал 403
        (на `/models` без тела это не видно, поэтому каталог работал).
        """
        chunks: List[bytes] = []
        while True:
            line = self.rfile.readline(65536)
            if not line:
                break
            size_token = line.strip().split(b";", 1)[0]
            try:
                size = int(size_token, 16)
            except ValueError:
                break
            if size == 0:
                # Трейлеры после последнего чанка — до пустой строки.
                while True:
                    trailer = self.rfile.readline(65536)
                    if not trailer or trailer in (b"\r\n", b"\n"):
                        break
                break
            chunk = self.rfile.read(size)
            if not chunk:
                break
            chunks.append(chunk)
            self.rfile.read(2)  # CRLF после чанка
        return b"".join(chunks) or None

    # ---------------------------------------------------------------- forward
    def _forward(self, method: str, local_path: str, target: str, request_headers: Dict[str, str]) -> None:
        """Прочитать body, переслать запрос на `target` и отдать ответ поточно."""
        body: Optional[bytes] = None
        length = _header(request_headers, "Content-Length")
        transfer_encoding = (_header(request_headers, "Transfer-Encoding") or "").lower()
        if "chunked" in transfer_encoding:
            try:
                body = self._read_chunked_body()
            except (OSError, ValueError):
                body = None
        elif length and str(length).isdigit():
            try:
                body = self.rfile.read(int(length)) or None
            except (OSError, ValueError):
                body = None

        rec = build_request_trace(method, local_path, target, body)
        t0 = time.time()

        hdrs = self._forward_headers(request_headers, body)
        cookie = self._proxy.cookie_header
        ctx = _ssl_context(self._proxy.insecure_tls)

        # Долгий кейс может пережить истечение Sudir-сессии: upstream тогда
        # вернёт 401 (HTML-страница Sber «Ошибка при аутентификации»), re-login
        # обновит банку кук, но текущий запрос при этом отдастся клиенту как
        # 401 -> opencode пометит APIError isRetryable=false и убьёт ход. Как в
        # desktop (apiFetch.maybeNotifyExpired) — на 401 сначала уведомляем
        # слушателя (re-login + set_cookie_header), затем повторяем запрос один
        # раз со свежими куками. Только повторный 401 отдаём клиенту.
        upstream = None
        for attempt in (0, 1):
            # Куки может обновить re-login на предыдущей итерации.
            if cookie:
                hdrs["Cookie"] = self._proxy.cookie_header
            req = urllib.request.Request(target, data=body, headers=hdrs, method=method)
            try:
                upstream = urllib.request.urlopen(req, timeout=self._proxy.upstream_timeout, context=ctx)
                break
            except urllib.error.HTTPError as e:
                try:
                    err_body = e.read()
                except Exception:  # noqa: BLE001
                    err_body = b""
                if e.code == 401:
                    e.close()
                    if attempt == 0:
                        # Первый 401 -> перелогин (synchr) и повтор через итерацию.
                        self._proxy._notify_expired(target)
                        continue
                    # Повторный 401 — свежие куки не помогли: сессия/бэк недоступен.
                    # Отдаём клиенту честный 401 с понятным JSON (не HTML Sber),
                    # чтобы retry-политика opencode не видела мусорную страницу.
                    self._respond(
                        401,
                        json.dumps({"error": "upstream 401 (session expired)", "code": 401}).encode(),
                    )
                    rec["status"] = 401
                    rec["error"] = "upstream 401 (session expired)"
                    self._trace_end(rec, t0)
                    return
                # Не 401: статус upstream доходит до sidecar как есть — от него
                # зависит retry-политика opencode (429 -> retry-after, 5xx -> retry).
                self._respond(e.code, err_body or json.dumps({"error": f"upstream HTTP {e.code}"}).encode())
                rec["status"] = e.code
                rec["error"] = f"upstream HTTP {e.code}"
                self._trace_end(rec, t0)
                e.close()
                return
            except Exception as e:  # noqa: BLE001
                self._respond(502, json.dumps({"error": "gateway unreachable", "detail": str(e)}).encode())
                rec["status"] = 502
                rec["error"] = str(e)
                self._trace_end(rec, t0)
                return

        if upstream is None:
            self._respond(502, json.dumps({"error": "gateway unreachable"}).encode())
            rec["status"] = 502
            rec["error"] = "gateway unreachable"
            self._trace_end(rec, t0)
            return

        # resp_buf объявляется ДО try: обрыв соединения до end_headers (клиент
        # оборвал стрим) оставлял переменную несвязанной, except-блок падал с
        # UnboundLocalError и маскировал реальную ошибку — POST-trace не попадал
        # в raw.log.
        resp_buf = bytearray()
        try:
            status = upstream.status
            content_type = upstream.headers.get("Content-Type", "application/json")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Transfer-Encoding", "chunked")
            # Прокидываем полезные upstream-заголовки (кроме hop-by-hop /
            # framing; Set-Cookie отдельно обновляет банку кук, но клиенту
            # тоже передаётся).
            for k, v in upstream.headers.items():
                if k.lower() in RESPONSE_STRIP_HEADERS:
                    continue
                self.send_header(k, v)
            # Set-Cookie -> в банку кук (как sessionService.applySetCookie).
            set_cookies = upstream.headers.get_all("Set-Cookie") or []
            if set_cookies:
                self._proxy.apply_set_cookie(set_cookies)
            # Как и в _respond: без keep-alive — закрываем соединение после
            # ответа, чтобы клиент не рвал idle-сокет (ConnectionResetError).
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while True:
                # read1, а не read: HTTPResponse.read(n) через BufferedReader
                # блокируется до набора n байт — SSE-дельты копились в прокси,
                # пока не накопится 64 КБ или не кончится ответ, opencode не
                # получал данных и рвал запрос сторожем («Model stalled»).
                # read1 отдаёт чанки по мере поступления (как upstream.pipe
                # в desktop proxy.ts).
                chunk = upstream.read1(65536)
                if not chunk:
                    self.wfile.write(b"0\r\n\r\n")
                    break
                self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii"))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                if len(resp_buf) < RESP_SNIPPET_BYTES:
                    resp_buf.extend(chunk[: RESP_SNIPPET_BYTES - len(resp_buf)])
            apply_response_trace(rec, status, content_type, bytes(resp_buf) or None)
        except Exception as e:  # noqa: BLE001
            # Стриминг прервался — НЕ дописываем финальный чанк `0\r\n\r\n`.
            # Финальный чанк сигналит клиенту «корректный конец чанкированного
            # ответа», и opencode воспримет неполный SSE как IncompleteRead, а
            # не как сбой, из-за чего оркестратор уходит в вечный ретрай
            # («Model stalled — retrying»).(`upstream.on("error")
            # -> res.destroy()`), рвём сокет клиенту,
            # чтобы тот увидел РЕАЛЬНЫЙ обрыв стрима.
            try:
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
            except Exception:  # noqa: BLE001
                pass
            self.close_connection = True
            rec["status"] = rec.get("status", 0)
            rec["error"] = "stream error: " + repr(e)
            apply_response_trace(rec, rec["status"], rec.get("content_type") or "unknown", bytes(resp_buf) or None)
        finally:
            upstream.close()
        self._trace_end(rec, t0)

    # ----------------------------------------------------------------- models
    def _fetch_models_raw(self, url: str) -> Optional[str]:
        """GET upstream /models (строка JSON) с ретраем после re-login.

        На 401 (истекла Sudir-сессия) сначала re-login через слушателя, затем
        повтор со свежими куками. Повторный 401 — отдаём ошибку. Без этого
        sidecar при протухшей сессии получает 401 и молча падает в дефолтный
        каталог из одного GigaChat-3-Ultra. Возвращает None, если ответ уже
        отправлен клиенту (ошибка upstream).
        """
        ctx = _ssl_context(self._proxy.insecure_tls)
        for attempt in (0, 1):
            # Пересоздаём Request на каждой итерации: re-login мог обновить
            # cookie_header, а urllib.Request.headers — read-only mapping.
            cookies = self._proxy.cookie_header
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": GIGACHAT_USER_AGENT,
                    **({"Cookie": cookies} if cookies else {}),
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self._proxy.upstream_timeout, context=ctx) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    e.close()
                    self._proxy._notify_expired(url)
                    continue
                self._models_fail(url, e.code, f"gateway /models HTTP {e.code}")
                e.close()
                return None
            except Exception as e:  # noqa: BLE001
                self._models_fail(url, 502, f"gateway /models unreachable: {e}", detail=str(e))
                return None
        return None

    def _models_fail(self, url: str, status: int, error: str, detail: Optional[str] = None) -> None:
        rec = build_request_trace("GET", self.path or "/models", url, None)
        rec["status"] = status
        rec["error"] = error
        self._proxy._trace_now(rec)
        payload = {"error": error}
        if detail:
            payload["detail"] = detail
        self._respond(status, json.dumps(payload).encode())

    def _serve_models(self) -> None:
        """GET /models — каталог моделей. Отдаём plain-массив RawModel[].

        Объекты шлюза отдаются как есть (как в desktop): из `limit.output`
        sidecar считает `max_tokens`, из `modalities.input` — vision-capabilities
        (видимость tool `read_image`), из `reasoning` — политику reasoning.
        """
        url = self._proxy.upstream.rstrip("/") + "/models"
        raw = self._fetch_models_raw(url)
        if raw is None:
            return
        try:
            body = json.loads(raw)
        except (ValueError, TypeError):
            self._models_fail(url, 502, "gateway /models returned non-JSON")
            return
        data = body.get("data") if isinstance(body, dict) else body
        if not isinstance(data, list):
            self._models_fail(url, 502, "gateway /models missing data array")
            return

        models = select_gateway_models(data)
        out = json.dumps(models).encode()
        self._respond(200, out, content_type="application/json")
        rec = build_request_trace("GET", self.path or "/models", url, None)
        apply_response_trace(rec, 200, "application/json", out)
        self._proxy._trace_now(rec)

    # ---------------------------------------------------------------- routes
    def _route(self, method: str) -> None:
        """Общая обёртка всех методов-обработчиков.

        HttpServer вызывает `do_*` в отдельной нити; если из него вылетает
        исключение (обрыв клиента, TimeoutError апстрима, битый ответ и т.п.),
        socketserver печатает длинный трейс в stderr и продолжает в других
        нитях — это «Exception occurred during processing of request» в логе.
        Ловим всё здесь и штатно закрываем соединение, как Node-прокси
        (`res.destroy()`): обрыв клиента не должен кричать в лог как ошибка,
        а трейс запроса всё равно пишется в raw.log внутри _forward/_serve_models.
        """
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.rstrip("/") == "/models":
                self._serve_models()
                return
            target = self._proxy.upstream.rstrip("/") + self.path
            self._forward(method, self.path, target, dict(self.headers.items()))
        except Exception:  # noqa: BLE001
            # Изолируем сбой уже-отвечавшего соединения. Не печатаем трейс в
            # stderr (иначе сокетсервер повторяет его в лог) — просто гасим.
            try:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
            except Exception:  # noqa: BLE001
                pass

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def do_PUT(self) -> None:
        self._route("PUT")

    def do_DELETE(self) -> None:
        self._route("DELETE")

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        pass


__all__ = [
    "GigaChatHandler",
    "GIGACHAT_USER_AGENT",
    "STRIP_HEADERS",
    "RESPONSE_STRIP_HEADERS",
]