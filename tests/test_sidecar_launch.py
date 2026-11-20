"""Запуск sidecar'а: лимит заголовков ответа, системный CA и TLS-политика.

Четыре точки, где снимается расхождение с desktop:

1. `--max-http-header-size` в команде спавна (`server.sidecar_command`) —
   дефолтный лимит Node/undici в 16 КиБ даёт `UND_ERR_HEADERS_OVERFLOW` на
   5 Set-Cookie от SberFriend gateway;
2. `--use-openssl-ca` там же — Electron доверяет корпоративному корню из
   хранилища ОС, голый Node проверяет только встроенный bundle Mozilla и
   роняет MCP `corpsearch` с `unable to verify the first certificate`;
3. `undici.Agent({maxHeaderSize})` в инъектированном fetch (`user_fetch.mjs`) —
   парити с `apiFetch` в desktop (apiClient.ts:218-221);
4. TLS-политика `shouldRejectUnauthorized` — порт apiClient.ts:205-228:
   корпоративные хосты и dev-канал идут через нестрогий агент.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from client.server.server import (
    HeadlessInstance,
    _SIDECAR_MAX_HEADER_SIZE,
    sidecar_banner,
    sidecar_command,
)

USER_FETCH = Path(__file__).resolve().parent.parent / "client" / "server" / "user_fetch.mjs"

FAKE_UNDICI = """
const calls = []

class Agent {
  constructor(options) {
    this.options = options
  }
}

async function fetchImpl(input, init) {
  const opts = init || {}
  const headers = new Headers(opts.headers || {})
  const connect = opts.dispatcher && opts.dispatcher.options ? opts.dispatcher.options.connect : undefined
  calls.push({
    url: typeof input === "string" ? input : String(input && input.url ? input.url : input),
    method: opts.method || "GET",
    cookie: headers.get("cookie"),
    userAgent: headers.get("user-agent"),
    origin: headers.get("origin"),
    referer: headers.get("referer"),
    maxHeaderSize: opts.dispatcher && opts.dispatcher.options ? opts.dispatcher.options.maxHeaderSize : null,
    rejectUnauthorized: connect ? connect.rejectUnauthorized : null,
  })
  return { ok: true, status: 200 }
}

module.exports = { Agent, fetch: fetchImpl, __calls: calls }
"""

NODE_SCRIPT = """
import { loadUndici, makeUserFetch, MAX_HEADER_SIZE } from "__USER_FETCH__"

const undici = loadUndici("__BUNDLE__")
const customFetch = makeUserFetch(undici)
if (!customFetch) {
  console.log(JSON.stringify({ error: "customFetch is undefined" }))
  process.exit(1)
}

await customFetch("http://127.0.0.1:12345/chat/completions", { method: "POST", body: "{}" })
await customFetch("https://shared.example/app/ai-assistant/fs-orchestrator-gateway/long/gigachat/v1")
await customFetch(new Request("https://shared.example/models"))
await customFetch("https://shared-api.ift-s.sberfriend.delta.sbrf.ru/sberfriend-api/api/v1/ext/app/se/rag-search/api/mcp/")

console.log(JSON.stringify({ maxHeaderSize: MAX_HEADER_SIZE, calls: undici.__calls }))
"""


class SidecarCommandTest(unittest.TestCase):
    def test_flag_raises_node_header_limit(self):
        cmd = sidecar_command("node", Path("/opt/app/desktop_launcher.mjs"))
        self.assertEqual(cmd[0], "node")
        self.assertIn(f"--max-http-header-size={_SIDECAR_MAX_HEADER_SIZE}", cmd)
        self.assertEqual(cmd[-1], "/opt/app/desktop_launcher.mjs")

    def test_limit_is_far_above_node_default(self):
        self.assertGreater(_SIDECAR_MAX_HEADER_SIZE, 16 * 1024)

    def test_system_ca_enabled(self):
        # Без системного CA голый Node не доверяет корпоративному корню:
        # MCP corpsearch падает на TLS, и промт теряет <mcp-routing-guidance>.
        cmd = sidecar_command("node", Path("/opt/app/desktop_launcher.mjs"))
        self.assertIn("--use-openssl-ca", cmd)


class SidecarBannerTest(unittest.TestCase):
    """Шапка server.log: чем реально стартовал node.

    Прогон, поднятый старым кодом, внешне неотличим от нового — кроме
    пропавших `corpsearch_*` в промте. Шапка фиксирует команду и TLS-окружение
    прямо в логе sidecar'а, чтобы это было видно без догадок.
    """

    def test_banner_shows_command_and_tls_env(self):
        cmd = sidecar_command("node", Path("/opt/app/desktop_launcher.mjs"))
        banner = sidecar_banner(cmd, {"NODE_EXTRA_CA_CERTS": "/tmp/ca.pem"})
        self.assertIn("--use-openssl-ca", banner)
        self.assertIn("/opt/app/desktop_launcher.mjs", banner)
        self.assertIn("NODE_EXTRA_CA_CERTS=/tmp/ca.pem", banner)
        self.assertTrue(banner.endswith("\n"))

    def test_banner_marks_absent_env(self):
        banner = sidecar_banner(["node", "-e", "0"], {})
        self.assertIn("NODE_EXTRA_CA_CERTS=-", banner)
        self.assertIn("NODE_OPTIONS=-", banner)

    def test_banner_has_no_secrets(self):
        # Команда и два имени env — секретов (куки, пароли) в шапке быть не может.
        banner = sidecar_banner(sidecar_command("node", Path("/l.mjs")), {"OPENCODE_SERVER_PASSWORD": "s3cr3t"})
        self.assertNotIn("s3cr3t", banner)


class SidecarEnvTest(unittest.TestCase):
    def test_functions_state_id_replay_disabled_like_desktop(self):
        # desktop: .env.development/.env.production → FUNCTIONS_STATE_ID_TRANSLATE
        # _DISABLED="true" → env sidecar'а (server.ts:449). Со включённым
        # реплеем gateway отдаёт 422 на втором подряд вызове инструмента.
        with tempfile.TemporaryDirectory() as tmp:
            inst = HeadlessInstance(work_dir=tmp, log_dir=Path(tmp) / "logs")
            env = inst._build_server_env("http://127.0.0.1:1")
        self.assertEqual(env["FUNCTIONS_STATE_ID_TRANSLATE_DISABLED"], "true")


class UserFetchTest(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("node недоступен")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        undici = self.root / "bundle" / "node_modules" / "undici"
        undici.mkdir(parents=True)
        (undici / "package.json").write_text(
            json.dumps({"name": "undici", "version": "0.0.0-fake", "main": "index.js"}),
            encoding="utf-8",
        )
        (undici / "index.js").write_text(FAKE_UNDICI, encoding="utf-8")
        self.bundle = self.root / "bundle" / "dist" / "node" / "node.js"
        self.script = self.root / "run.mjs"
        self.script.write_text(
            NODE_SCRIPT.replace("__USER_FETCH__", USER_FETCH.as_uri()).replace("__BUNDLE__", str(self.bundle)),
            encoding="utf-8",
        )

    def _run(self, channel: str = "dev"):
        env = dict(os.environ)
        env["WSC_SESSION_COOKIES"] = "PLATFORM_SESSION=abc"
        env["WSC_SHARED_API_ORIGIN"] = "https://shared.example"
        env["OPENCODE_CHANNEL"] = channel
        env.pop("WSC_SESSION_COOKIES_FILE", None)
        proc = subprocess.run(
            [self.node, str(self.script)], capture_output=True, text=True, env=env, timeout=60
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_every_request_goes_through_raised_header_limit(self):
        out = self._run()
        self.assertEqual(out["maxHeaderSize"], _SIDECAR_MAX_HEADER_SIZE)
        limits = [call["maxHeaderSize"] for call in out["calls"]]
        self.assertEqual(len(limits), 4, out["calls"])
        self.assertEqual(limits, [_SIDECAR_MAX_HEADER_SIZE] * 4)

    def test_corp_host_skips_chain_verification(self):
        # apiClient.ts: корпоративные хосты идут через
        # Agent({ connect: { rejectUnauthorized: false } }) — на тестовых
        # стендах лист выпущен внутренним CA, которого нет в хранилище ОС.
        *_, corp = self._run(channel="prod")["calls"]
        self.assertIn("sberfriend.delta.sbrf.ru", corp["url"])
        self.assertIs(corp["rejectUnauthorized"], False)

    def test_public_host_is_verified_on_prod_channel(self):
        # Не корпоративный хост на прод-канале: строгий агент, без послабления.
        external = self._run(channel="prod")["calls"][1]
        self.assertIn("shared.example", external["url"])
        self.assertIsNone(external["rejectUnauthorized"])

    def test_dev_channel_skips_verification_like_electron(self):
        # app.ts: на dev-канале `certificate-error` игнорируется для любого
        # хоста, поэтому строгого агента на dev просто нет.
        out = self._run(channel="dev")
        self.assertTrue(all(call["rejectUnauthorized"] is False for call in out["calls"]), out["calls"])

    def test_loopback_keeps_no_cookies(self):
        loopback, external, request_input, _corp = self._run()["calls"]
        self.assertTrue(loopback["url"].startswith("http://127.0.0.1:"))
        self.assertIsNone(loopback["cookie"])
        self.assertEqual(loopback["method"], "POST")

        self.assertEqual(external["cookie"], "PLATFORM_SESSION=abc")
        self.assertEqual(external["userAgent"].split(" ")[0], "Mozilla/5.0")
        self.assertEqual(external["origin"], "https://shared.example")
        self.assertEqual(external["referer"], "https://shared.example/")

        self.assertEqual(request_input["url"], "https://shared.example/models")
        self.assertEqual(request_input["cookie"], "PLATFORM_SESSION=abc")


if __name__ == "__main__":
    unittest.main()
