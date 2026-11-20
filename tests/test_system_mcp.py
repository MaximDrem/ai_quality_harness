"""Платформенные MCP-серверы: подстановка env и стемпы как в desktop."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from client.server import system_mcp


class CorpSearchUrlTest(unittest.TestCase):
    def test_base_path_taken_from_corp_search_url(self) -> None:
        url = system_mcp.corp_search_mcp_url(
            "https://shared-api-sigma-sberfriend.sberbank.ru",
            "https://shared-api-sigma-sberfriend.sberbank.ru/sberfriend-api/api/v1/ext"
            "/app/se/search-engine/api/v5/find",
        )
        self.assertEqual(
            url,
            "https://shared-api-sigma-sberfriend.sberbank.ru/sberfriend-api/api/v1/ext"
            "/app/se/rag-search/api/mcp/",
        )

    def test_origin_fallback_without_corp_search_url(self) -> None:
        url = system_mcp.corp_search_mcp_url("https://example.test/")
        self.assertEqual(url, "https://example.test/sberfriend-api/api/v1/ext/app/se/rag-search/api/mcp/")


class LoadSystemMcpTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, payload: dict) -> Path:
        path = self.root / "system-mcp.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_placeholder_substituted_and_stamped(self) -> None:
        path = self._write(
            {
                "corpsearch": {"type": "remote", "url": "${CORP_SEARCH_MCP_URL}", "useUserCreds": True, "oauth": False},
                "efir": {"type": "remote", "url": "https://efir.sber.ru/mcp", "oauth": {"clientId": "x"}},
            }
        )

        result = system_mcp.load_system_mcp(
            path,
            values={"CORP_SEARCH_MCP_URL": "https://mcp.test/corp"},
            enabled=("corpsearch",),
        )

        self.assertEqual(sorted(result), ["corpsearch", "efir"])
        self.assertEqual(result["corpsearch"]["url"], "https://mcp.test/corp")
        self.assertTrue(result["corpsearch"]["system"])
        self.assertTrue(result["corpsearch"]["enabled"])
        self.assertFalse(result["efir"]["enabled"])
        self.assertTrue(result["efir"]["system"])

    def test_unresolved_placeholder_drops_server(self) -> None:
        path = self._write({"corpsearch": {"type": "remote", "url": "${MISSING}/mcp"}})

        result = system_mcp.load_system_mcp(path, values={}, enabled=("corpsearch",))

        self.assertEqual(result, {})

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(system_mcp.load_system_mcp(self.root / "absent.json"), {})

    def test_enabled_names(self) -> None:
        self.assertEqual(system_mcp._enabled_names(None), system_mcp.DEFAULT_ENABLED)
        self.assertEqual(system_mcp._enabled_names(""), system_mcp.DEFAULT_ENABLED)
        self.assertEqual(system_mcp._enabled_names("none"), ())
        self.assertEqual(system_mcp._enabled_names("corpsearch, efir"), ("corpsearch", "efir"))


if __name__ == "__main__":
    unittest.main()
