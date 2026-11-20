"""Дамп тел запросов прокси: имя по sha1, дедуп и устойчивость к ошибкам."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from client.server.proxy import tracing


class DumpRequestBodyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / tracing.DUMP_DIR_NAME

    def test_file_named_by_content_hash(self) -> None:
        body = b'{"model":"x","messages":[]}'

        path = tracing.dump_request_body(self.dir, body)

        name = hashlib.sha1(body).hexdigest()[:12] + ".json"
        self.assertEqual(path, str(self.dir / name))
        self.assertEqual(Path(path).read_bytes(), body)

    def test_same_body_writes_single_file(self) -> None:
        body = b'{"messages":[{"role":"system","content":"x"}]}'

        first = tracing.dump_request_body(self.dir, body)
        second = tracing.dump_request_body(self.dir, body)

        self.assertEqual(first, second)
        self.assertEqual(len(list(self.dir.iterdir())), 1)

    def test_empty_body_writes_nothing(self) -> None:
        self.assertEqual(tracing.dump_request_body(self.dir, b""), "")
        self.assertEqual(tracing.dump_request_body(self.dir, None), "")
        self.assertFalse(self.dir.exists())


if __name__ == "__main__":
    unittest.main()
