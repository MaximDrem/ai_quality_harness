"""Прозрачный tee перед mTLS-прокси desktop (режим `cert`).

В `cert` авторизация к моделям идёт по сертификату через `scripts/proxy.ts`
(bun), и запросы sidecar'а минуют Python. Из-за этого не работал дамп тел
(`<run>/requests/<sha1>.json`) и трейс в raw.log: их пишет cookie-прокси,
которого в этом режиме нет.

Tee вставляет один loopback-хоп между sidecar'ом и bun-прокси:

    sidecar -> tee (Python) -> scripts/proxy.ts (bun, mTLS) -> GigaChat

Тело запроса читается целиком (bun всё равно буферизует его `express.raw`),
сохраняется в дамп и уходит дальше **байт-в-байт**; ответ стримится обратно
чанками. Из заголовков снимаются только framing/hop-by-hop, которые нельзя
ретранслировать, — всё остальное (UA, Origin, Accept-Encoding) повторяется
как есть, поэтому поведение цепочки совпадает с desktop.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional, Union

from .proxy import ThreadingHTTPServer, free_port, is_port_open
from .tracing import (
    RESP_SNIPPET_BYTES,
    apply_response_trace,
    build_request_trace,
    dump_request_body,
    finish_trace,
)

# Framing/hop-by-hop заголовки запроса: их нельзя ретранслировать (тело
# пересобрано, соединение новое). Остальные заголовки идут как есть.
STRIP_REQUEST_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
}

# Framing-заголовки ответа: ответ переупаковывается в chunked. `content-encoding`
# НЕ снимается — тело ретранслируется в том виде, в каком пришло.
STRIP_RESPONSE_HEADERS = {
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
}

_TRACE_TAIL = 1000


def _read_chunked_body(rfile) -> Optional[bytes]:
    """Прочитать тело запроса с `Transfer-Encoding: chunked`."""
    chunks: List[bytes] = []
    while True:
        line = rfile.readline(65536)
        if not line:
            break
        size_token = line.strip().split(b";", 1)[0]
        try:
            size = int(size_token, 16)
        except ValueError:
            break
        if size == 0:
            while True:
                trailer = rfile.readline(65536)
                if not trailer or trailer in (b"\r\n", b"\n"):
                    break
            break
        chunk = rfile.read(size)
        if not chunk:
            break
        chunks.append(chunk)
        rfile.read(2)  # CRLF после чанка
    return b"".join(chunks) or None


class TeeHandler(http.server.BaseHTTPRequestHandler):
    """Слепой форвард на bun-прокси с дампом тела и трейсом."""

    protocol_version = "HTTP/1.1"

    @property
    def _proxy(self) -> "TeeProxy":
        return self.server._proxy  # type: ignore[attr-defined]

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.close_connection = True
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _read_body(self) -> Optional[bytes]:
        length = self.headers.get("Content-Length")
        transfer_encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in transfer_encoding:
            return _read_chunked_body(self.rfile)
        if length and str(length).isdigit():
            try:
                return self.rfile.read(int(length)) or None
            except (OSError, ValueError):
                return None
        return None

    def _send_buffered(self, upstream, payload: bytes) -> None:
        """Отдать буферизованный ответ (каталог моделей) с Content-Length."""
        self.send_response(upstream.status)
        for k, v in upstream.headers.items():
            if k.lower() in STRIP_RESPONSE_HEADERS:
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(payload)
        self.wfile.flush()

    def _forward(self, method: str) -> None:
        body = self._read_body()
        target = self._proxy.upstream + self.path
        rec = build_request_trace(method, self.path, target, body)
        dump_dir = self._proxy.dump_dir
        if dump_dir is not None:
            dumped = dump_request_body(dump_dir, body)
            if dumped:
                rec["dump"] = dumped
        t0 = time.time()

        headers = {
            k: v
            for k, v in dict(self.headers.items()).items()
            if k.lower() not in STRIP_REQUEST_HEADERS
        }
        req = urllib.request.Request(target, data=body, headers=headers, method=method)
        try:
            upstream = urllib.request.urlopen(req, timeout=self._proxy.upstream_timeout)
        except urllib.error.HTTPError as err:
            try:
                err_body = err.read()
            except Exception:  # noqa: BLE001
                err_body = b""
            self._respond(err.code, err_body or json.dumps({"error": f"proxy HTTP {err.code}"}).encode())
            rec["status"] = err.code
            rec["error"] = f"proxy HTTP {err.code}"
            self._proxy._trace_now(finish_trace(rec, t0))
            err.close()
            return
        except Exception as err:  # noqa: BLE001
            self._respond(502, json.dumps({"error": "cert proxy unreachable", "detail": str(err)}).encode())
            rec["status"] = 502
            rec["error"] = str(err)
            self._proxy._trace_now(finish_trace(rec, t0))
            return

        resp_buf = bytearray()
        # Каталог `/models` от `scripts/proxy.ts` — фиктивные limit-ы
        # (`context: 131072, output: 8192` на все модели). В IFT режиме opencode
        # получает настоящий каталог шлюза, поэтому в cert подменяем `limit`
        # значениями из эталонного каталога: max_tokens и окно контекста
        # становятся такими же, как в IFT.
        is_models = urllib.parse.urlparse(self.path).path.rstrip("/") == "/models"
        content_encoding = (upstream.headers.get("Content-Encoding") or "identity").lower()
        try:
            status = upstream.status
            content_type = upstream.headers.get("Content-Type", "application/json")
            if is_models and content_encoding in ("", "identity"):
                payload, rewritten = self._proxy.rewrite_models(upstream.read())
                if rewritten:
                    rec["models_limits"] = "reference"
                self._send_buffered(upstream, payload)
                resp_buf.extend(payload[:RESP_SNIPPET_BYTES])
            else:
                self.send_response(status)
                for k, v in upstream.headers.items():
                    if k.lower() in STRIP_RESPONSE_HEADERS:
                        continue
                    self.send_header(k, v)
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                while True:
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
        except Exception as err:  # noqa: BLE001
            # Стрим оборвался: не дописываем финальный чанк, чтобы sidecar
            # увидел реальный обрыв, а не «корректный конец» SSE.
            try:
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
            except Exception:  # noqa: BLE001
                pass
            self.close_connection = True
            rec["status"] = rec.get("status", 0)
            rec["error"] = "stream error: " + repr(err)
            apply_response_trace(rec, rec["status"], rec.get("content_type") or "unknown", bytes(resp_buf) or None)
        finally:
            upstream.close()
        self._proxy._trace_now(finish_trace(rec, t0))

    def _route(self, method: str) -> None:
        try:
            self._forward(method)
        except Exception:  # noqa: BLE001
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


class TeeProxy:
    """Loopback-tee перед bun-прокси: форвард + дамп тел + трейс."""

    def __init__(
        self,
        upstream: str,
        port: Optional[int] = None,
        upstream_timeout: float = 300.0,
        limits_provider: Optional[Callable[[], Optional[Dict[str, dict]]]] = None,
    ):
        self.upstream = upstream.rstrip("/")
        self.port = port or free_port()
        self.upstream_timeout = upstream_timeout
        self.base_url: Optional[str] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._sink: Optional[Callable[[Dict], None]] = None
        self._dump_dir: Optional[str] = None
        self._trace_lock = threading.Lock()
        self._trace: List[Dict] = []
        # Эталонный каталог (`{model_id: limit}`) для подмены фиктивных limit-ов
        # bun-прокси значениями IFT. Загружается лениво, кэшируется до успеха.
        self._limits_provider = limits_provider
        self._limits_lock = threading.Lock()
        self._limits: Optional[Dict[str, dict]] = None

    # ------------------------------------------------------------- listeners
    def attach_logger(
        self,
        sink: Callable[[Dict], None],
        dump_dir: Optional[Union[str, "os.PathLike[str]"]] = None,
    ) -> None:
        """Привязать sink трейса и каталог полных тел запросов."""
        with self._trace_lock:
            self._sink = sink
            self._dump_dir = str(dump_dir) if dump_dir is not None else None

    @property
    def dump_dir(self) -> Optional[str]:
        return self._dump_dir

    # ------------------------------------------------------------- models
    def reference_limits(self) -> Optional[Dict[str, dict]]:
        """`{model_id: limit}` из эталонного каталога (IFT).

        Кэшируется только успешный ответ: если каталог недоступен (например,
        Sudir-куки ещё не обновились), следующее обращение к `/models` сходит
        за ним повторно.
        """
        if self._limits_provider is None:
            return None
        with self._limits_lock:
            if self._limits is None:
                try:
                    self._limits = self._limits_provider()
                except Exception:  # noqa: BLE001 — каталог не должен ломать прокси
                    self._limits = None
            return self._limits

    def rewrite_models(self, payload: bytes) -> tuple[bytes, bool]:
        """Подменить `limit` моделей значениями эталона. (тело, изменено ли)."""
        limits = self.reference_limits()
        if not limits:
            return payload, False
        try:
            items = json.loads(payload)
        except (ValueError, TypeError):
            return payload, False
        if not isinstance(items, list):
            return payload, False
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            limit = limits.get(item.get("id"))
            if limit and item.get("limit") != limit:
                item["limit"] = limit
                changed = True
        if not changed:
            return payload, False
        return json.dumps(items, ensure_ascii=False).encode("utf-8"), True

    def _trace_now(self, rec: Dict) -> None:
        with self._trace_lock:
            if self._sink:
                try:
                    self._sink(rec)
                except Exception:  # noqa: BLE001 — трейс не должен ломать прокси
                    pass
            self._trace.append(rec)
            if len(self._trace) > _TRACE_TAIL:
                self._trace = self._trace[-_TRACE_TAIL:]

    # --------------------------------------------------------------- lifecycle
    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    def start(self, log_dir=None, wait_ready: bool = True) -> "TeeProxy":
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), TeeHandler)
        self._server._proxy = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self.port}"
        if wait_ready:
            deadline = time.time() + 10.0
            while time.time() < deadline:
                if is_port_open(self.port):
                    return self
                time.sleep(0.05)
            raise RuntimeError(f"tee did not start listening on {self.port}")
        return self

    def stop(self, timeout: float = 10.0) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            finally:
                self._server.server_close()
        self._server = None
        self._thread = None


__all__ = ["STRIP_REQUEST_HEADERS", "STRIP_RESPONSE_HEADERS", "TeeHandler", "TeeProxy"]