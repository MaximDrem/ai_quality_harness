"""Tee перед mTLS-прокси: тело ретранслируется байт-в-байт, дамп и трейс пишутся."""

from __future__ import annotations

import hashlib
import http.client
import http.server
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from client.server.proxy import ThreadingHTTPServer
from client.server.proxy.tee import TeeProxy


class _UpstreamHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _record(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.seen.append(  # type: ignore[attr-defined]
            {"path": self.path, "headers": dict(self.headers.items()), "body": body}
        )
        return body

    def do_POST(self) -> None:  # noqa: N802
        body = self._record()
        payload = json.dumps({"echo": body.decode(), "ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/models":
            payload = json.dumps(self.server.models).encode()  # type: ignore[attr-defined]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self._record()
        chunks = [b"data: one\n\n", b"data: two\n\n"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        pass


class _Upstream:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        self.server.seen = []  # type: ignore[attr-defined]
        self.server.models = [  # type: ignore[attr-defined]
            {"id": "m1", "name": "m1", "limit": {"context": 131072, "output": 8192}},
        ]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class TeeProxyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dump_dir = Path(self._tmp.name) / "requests"

        self.upstream = _Upstream()
        self.addCleanup(self.upstream.stop)
        self.limits = {"value": None, "raise": False}

        def provider():
            if self.limits["raise"]:
                raise RuntimeError("catalog down")
            return self.limits["value"]

        self.tee = TeeProxy(self.upstream.url, limits_provider=provider).start()
        self.addCleanup(self.tee.stop)
        self.traces: list = []
        self.tee.attach_logger(self.traces.append, dump_dir=self.dump_dir)

    def _wait_for_trace(self, expected: int, timeout: float = 5.0) -> None:
        # Трейс пишется после отправки ответа: клиент может прочитать тело
        # раньше, чем запись дойдёт до sink.
        deadline = time.time() + timeout
        while len(self.traces) < expected and time.time() < deadline:
            time.sleep(0.01)
        if len(self.traces) < expected:
            self.fail(f"трейс не записан: {len(self.traces)} из {expected}")

    def _post(self, body: bytes) -> bytes:
        expected = len(self.traces) + 1
        conn = http.client.HTTPConnection("127.0.0.1", self.tee.port, timeout=10)
        self.addCleanup(conn.close)
        conn.request(
            "POST",
            "/chat/completions",
            body=body,
            headers={"Content-Type": "application/json", "X-Test": "1"},
        )
        payload = conn.getresponse().read()
        self._wait_for_trace(expected)
        return payload

    def test_body_reaches_upstream_unchanged(self) -> None:
        body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "привет"}]}).encode()

        echoed = self._post(body)

        self.assertEqual(json.loads(echoed)["echo"], body.decode())
        seen = self.upstream.server.seen[-1]  # type: ignore[attr-defined]
        self.assertEqual(seen["body"], body)
        self.assertEqual(seen["path"], "/chat/completions")
        self.assertEqual(seen["headers"].get("X-Test"), "1")
        self.assertEqual(seen["headers"].get("Content-Length"), str(len(body)))

    def test_dump_is_named_by_content_hash(self) -> None:
        body = b'{"model":"m","messages":[]}'

        self._post(body)

        digest = hashlib.sha1(body).hexdigest()[:12]
        dumped = self.dump_dir / f"{digest}.json"
        self.assertTrue(dumped.is_file())
        self.assertEqual(dumped.read_bytes(), body)

    def test_trace_records_request_and_response(self) -> None:
        body = json.dumps(
            {
                "model": "m",
                "stream": True,
                "functions": [{"name": "read_file"}, {"name": "write_file"}],
                "messages": [{"role": "user", "content": "x"}],
            }
        ).encode()

        self._post(body)

        rec = self.traces[-1]
        self.assertEqual(rec["model"], "m")
        self.assertEqual(rec["functions"], ["read_file", "write_file"])
        self.assertEqual(rec["status"], 200)
        self.assertTrue(rec["dump"].endswith(".json"))

    def test_chunked_request_body(self) -> None:
        parts = [b'{"model":', b'"m","messages":[]}']

        conn = http.client.HTTPConnection("127.0.0.1", self.tee.port, timeout=10)
        self.addCleanup(conn.close)
        conn.putrequest("POST", "/chat/completions")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Transfer-Encoding", "chunked")
        conn.endheaders()
        for part in parts:
            conn.send(f"{len(part):x}\r\n".encode("ascii") + part + b"\r\n")
        conn.send(b"0\r\n\r\n")
        response = conn.getresponse()

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["echo"], b"".join(parts).decode())
        self.assertEqual(self.upstream.server.seen[-1]["body"], b"".join(parts))  # type: ignore[attr-defined]

    def test_streaming_response_is_relayed(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.tee.port, timeout=10)
        self.addCleanup(conn.close)
        conn.request("GET", "/chat/completions")

        response = conn.getresponse()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        self.assertEqual(response.read(), b"data: one\n\ndata: two\n\n")

    def _get_models(self) -> list:
        expected = len(self.traces) + 1
        conn = http.client.HTTPConnection("127.0.0.1", self.tee.port, timeout=10)
        self.addCleanup(conn.close)
        conn.request("GET", "/models")
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        models = json.loads(response.read())
        self._wait_for_trace(expected)
        return models

    def test_models_limits_replaced_from_reference(self) -> None:
        self.upstream.server.models = [  # type: ignore[attr-defined]
            {"id": "m1", "name": "m1", "limit": {"context": 131072, "output": 8192}},
            {"id": "m2", "name": "m2", "limit": {"context": 131072, "output": 8192}},
        ]
        reference = {"context": 262144, "output": 65536}
        self.limits["value"] = {"m1": reference}

        models = {m["id"]: m for m in self._get_models()}

        self.assertEqual(models["m1"]["limit"], reference)
        self.assertEqual(models["m2"]["limit"], {"context": 131072, "output": 8192})
        self.assertEqual(models["m1"]["name"], "m1")
        self.assertEqual(self.traces[-1]["models_limits"], "reference")

    def test_models_untouched_without_reference(self) -> None:
        models = self._get_models()

        self.assertEqual(models[0]["limit"], {"context": 131072, "output": 8192})
        self.assertNotIn("models_limits", self.traces[-1])

    def test_models_untouched_when_reference_fails(self) -> None:
        self.limits["raise"] = True

        models = self._get_models()

        self.assertEqual(models[0]["limit"], {"context": 131072, "output": 8192})
        self.assertNotIn("models_limits", self.traces[-1])

    def test_reference_is_retried_until_success(self) -> None:
        self.limits["raise"] = True
        self.assertIsNone(self.tee.reference_limits())

        self.limits["raise"] = False
        self.limits["value"] = {"m1": {"context": 5, "output": 6}}

        self.assertEqual(self.tee.reference_limits(), {"m1": {"context": 5, "output": 6}})

    def test_reference_is_fetched_once(self) -> None:
        calls = []

        def provider():
            calls.append(1)
            return {"m1": {"context": 1, "output": 2}}

        tee = TeeProxy(self.upstream.url, limits_provider=provider).start()
        self.addCleanup(tee.stop)

        self.assertEqual(tee.reference_limits(), {"m1": {"context": 1, "output": 2}})
        self.assertEqual(tee.reference_limits(), {"m1": {"context": 1, "output": 2}})
        self.assertEqual(len(calls), 1)

    def test_no_dump_dir_keeps_trace_only(self) -> None:
        self.tee.attach_logger(self.traces.append, dump_dir=None)

        self._post(b'{"model":"m"}')

        self.assertFalse(self.dump_dir.exists())
        self.assertNotIn("dump", self.traces[-1])


if __name__ == "__main__":
    unittest.main()
