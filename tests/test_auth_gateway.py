"""Границы `auth_mode`: модели — по режиму, MCP/профиль/поиск — всегда ИФТ.

Инвариант (суженный scope): `auth_mode` переключает ТОЛЬКО транспорт к
моделям и канал сборки sidecar'а. Профиль пользователя, платформенные MCP
(`corpsearch`) и корпоративный поиск берутся из dev-конфигурации desktop и
всегда ходят на ИФТ — иначе системные промты разойдутся между режимами.

Вторая половина — разделение сессий: IFT-куки (профиль/MCP/`customFetch`)
не должны попадать в прокси шлюза в `prod`, а прод-куки шлюза — в env
sidecar'а.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client.core.config import ClientConfig
from client.core.errors import InstanceError
from client.auth.electron_login import ElectronLogin
from client.server.proxy import GigachatProxy
from client.server.server import HeadlessInstance

IFT = "https://shared-api.ift-s.sberfriend.delta.sbrf.ru"
PROD = "https://shared-api-sigma-sberfriend.sberbank.ru"
BASE_PATH = "/sberfriend-api/api/v1/ext"
GATEWAY = BASE_PATH + "/app/ai-assistant/fs-orchestrator-gateway/long/gigachat/v1"

ENV = {
    "SHARED_API_URL": IFT,
    "GIGATOOL_PROD_SHARED_API_URL_ORIGIN": PROD,
    # Явные пустые значения: не тащим override'ы из окружения разработчика.
    "GIGATOOL_GIGACHAT_UPSTREAM": "",
    "GIGATOOL_PROD_BASE_PATH": "",
}


class _EnvMixin:
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, ENV, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._saved_cookies = os.environ.get("GIGACHAT_SESSION_COOKIES")
        self.addCleanup(self._restore_cookies)

    def _restore_cookies(self) -> None:
        if self._saved_cookies is None:
            os.environ.pop("GIGACHAT_SESSION_COOKIES", None)
        else:
            os.environ["GIGACHAT_SESSION_COOKIES"] = self._saved_cookies


class PinnedToIftTest(_EnvMixin, unittest.TestCase):
    """Профиль/MCP/поиск не зависят от режима — эталон dev-ветки desktop."""

    def test_shared_api_origin_is_ift_in_every_mode(self) -> None:
        for mode in ("ift", "cert", "prod"):
            with self.subTest(mode=mode):
                cfg = ClientConfig(auth_mode=mode)
                self.assertEqual(cfg.shared_api_origin, IFT)
                self.assertEqual(
                    cfg.current_person_url,
                    IFT + "/sberfriend-api/api/v1/ext/app/features/current/person",
                )
                self.assertEqual(
                    cfg.corp_search_url,
                    IFT + "/sberfriend-api/api/v1/ext/app/se/search-engine/api/v5/find",
                )

    def test_channel_and_backend_follow_mode(self) -> None:
        self.assertEqual(ClientConfig(auth_mode="ift").opencode_channel, "dev")
        self.assertEqual(ClientConfig(auth_mode="cert").opencode_channel, "dev")
        self.assertTrue(ClientConfig(auth_mode="ift").experimental_httpapi)

        prod = ClientConfig(auth_mode="prod")
        self.assertEqual(prod.opencode_channel, "prod")
        self.assertFalse(prod.experimental_httpapi)


class GatewayUpstreamTest(_EnvMixin, unittest.TestCase):
    """Меняется только upstream шлюза моделей."""

    def test_ift_and_cert_keep_ift_gateway(self) -> None:
        for mode in ("ift", "cert"):
            with self.subTest(mode=mode):
                cfg = ClientConfig(auth_mode=mode)
                self.assertEqual(cfg.gigachat_upstream, IFT + GATEWAY)
                self.assertEqual(cfg.gateway_origin, IFT)

    def test_prod_uses_prod_gateway_only(self) -> None:
        cfg = ClientConfig(auth_mode="prod")
        self.assertEqual(cfg.gigachat_upstream, PROD + GATEWAY)
        self.assertEqual(cfg.gateway_origin, PROD)
        # Профиль/MCP при этом остаются на ИФТ (см. PinnedToIftTest).
        self.assertEqual(cfg.shared_api_origin, IFT)

    def test_prod_base_path_override(self) -> None:
        with mock.patch.dict(os.environ, {"GIGATOOL_PROD_BASE_PATH": "/sberfriend-api/api/v1/int"}):
            cfg = ClientConfig(auth_mode="prod")
        self.assertEqual(
            cfg.gigachat_upstream,
            PROD + "/sberfriend-api/api/v1/int"
            "/app/ai-assistant/fs-orchestrator-gateway/long/gigachat/v1",
        )

    def test_prod_requires_own_origin(self) -> None:
        with mock.patch.dict(os.environ, {"GIGATOOL_PROD_SHARED_API_URL_ORIGIN": ""}):
            with self.assertRaises(InstanceError):
                ClientConfig(auth_mode="prod")

    def test_explicit_upstream_override_wins(self) -> None:
        explicit = "http://127.0.0.1:3128/v1"
        with mock.patch.dict(os.environ, {"GIGATOOL_GIGACHAT_UPSTREAM": explicit}):
            cfg = ClientConfig(auth_mode="prod")
        self.assertEqual(cfg.gigachat_upstream, explicit)


class ProdSplitTest(_EnvMixin, unittest.TestCase):
    """В `prod` sidecar и инъекции получают ИФТ, прод-origin — только прокси."""

    def _instance(self, root: Path) -> HeadlessInstance:
        cfg = ClientConfig(
            auth_mode="prod",
            session_cookies={"PLATFORM_SESSION": "ift-session"},
            gateway_cookies={"PLATFORM_SESSION": "prod-session"},
        ).resolve()
        return HeadlessInstance(work_dir=str(root), cfg=cfg, log_dir=root / "logs")

    def test_sidecar_env_stays_on_ift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inst = self._instance(Path(tmp))
            env = inst._build_server_env("http://127.0.0.1:9")

        self.assertEqual(env["SHARED_API_URL"], IFT)
        self.assertEqual(env["WSC_SHARED_API_ORIGIN"], IFT)
        self.assertEqual(env["CORP_SEARCH_URL"], IFT + BASE_PATH + "/app/se/search-engine/api/v5/find")
        self.assertEqual(env["WSC_SESSION_COOKIES"], "PLATFORM_SESSION=ift-session")
        self.assertEqual(env["GIGACHAT_BASE_URL"], "http://127.0.0.1:9")
        self.assertEqual(inst.cfg.gigachat_upstream, PROD + GATEWAY)

    def test_system_mcp_injection_stays_on_ift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inst = self._instance(Path(tmp))
            entries = inst._injections().get("systemMcp") or {}

        corpsearch = entries.get("corpsearch")
        if not corpsearch:
            self.skipTest("resources/system-mcp.json недоступен в этом окружении")
        self.assertEqual(corpsearch["url"], IFT + BASE_PATH + "/app/se/rag-search/api/mcp/")
        self.assertTrue(corpsearch["enabled"])
        self.assertEqual(inst.enabled_system_mcp(), ("corpsearch",))


class ElectronLoginOriginTest(_EnvMixin, unittest.TestCase):
    """Окно входа даёт сессию шлюза, значит открывается на его origin."""

    def test_prod_window_opens_on_prod_gateway(self) -> None:
        cfg = ClientConfig(auth_mode="prod").resolve()
        self.assertEqual(ElectronLogin(cfg).origin, PROD)

    def test_other_modes_keep_ift_window(self) -> None:
        for mode in ("ift", "cert"):
            with self.subTest(mode=mode):
                cfg = ClientConfig(auth_mode=mode).resolve()
                self.assertEqual(ElectronLogin(cfg).origin, IFT)


class GatewayCookiesTest(_EnvMixin, unittest.TestCase):
    """IFT-сессия и сессия шлюза не перетирают друг друга."""

    class _FakeProxy:
        def __init__(self) -> None:
            self.header = None

        def set_cookie_header(self, header: str) -> None:
            self.header = header

    def _prod_instance(self, root: Path) -> HeadlessInstance:
        cfg = ClientConfig(
            auth_mode="prod",
            session_cookies={"PLATFORM_SESSION": "ift-session"},
            gateway_cookies={"PLATFORM_SESSION": "prod-session"},
        ).resolve()
        inst = HeadlessInstance(work_dir=str(root), cfg=cfg, log_dir=root)
        inst.proxy = self._FakeProxy()
        return inst

    def test_proxy_prefers_gateway_cookies(self) -> None:
        cfg = ClientConfig(
            auth_mode="prod",
            session_cookies={"PLATFORM_SESSION": "ift-session"},
            gateway_cookies={"PLATFORM_SESSION": "prod-session"},
        )
        proxy = GigachatProxy(cfg)
        self.assertEqual(proxy.cookie_header, "PLATFORM_SESSION=prod-session")

    def test_proxy_falls_back_to_ift_session(self) -> None:
        cfg = ClientConfig(auth_mode="ift", session_cookies={"PLATFORM_SESSION": "ift-session"})
        proxy = GigachatProxy(cfg)
        self.assertEqual(proxy.cookie_header, "PLATFORM_SESSION=ift-session")

    def test_ift_relogin_does_not_touch_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inst = self._prod_instance(Path(tmp))
            inst._apply_relogin("PLATFORM_SESSION=ift-fresh")

            self.assertEqual(os.environ["GIGACHAT_SESSION_COOKIES"], "PLATFORM_SESSION=ift-fresh")
            self.assertEqual(inst.cfg.session_cookies, {"PLATFORM_SESSION": "ift-fresh"})
            self.assertEqual(inst.cfg.gateway_cookies, {"PLATFORM_SESSION": "prod-session"})
            self.assertIsNone(inst.proxy.header)
            self.assertEqual(
                (Path(tmp) / "session-cookies.txt").read_text(encoding="utf-8"),
                "PLATFORM_SESSION=ift-fresh",
            )

    def test_gateway_relogin_does_not_touch_ift_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inst = self._prod_instance(Path(tmp))
            inst._apply_gateway_relogin("PLATFORM_SESSION=prod-fresh")

            self.assertEqual(inst.proxy.header, "PLATFORM_SESSION=prod-fresh")
            self.assertEqual(inst.cfg.gateway_cookies, {"PLATFORM_SESSION": "prod-fresh"})
            self.assertEqual(inst.cfg.session_cookies, {"PLATFORM_SESSION": "ift-session"})
            self.assertNotIn("GIGACHAT_SESSION_COOKIES", os.environ)
            self.assertEqual(
                (Path(tmp) / "gateway-cookies.txt").read_text(encoding="utf-8"),
                "PLATFORM_SESSION=prod-fresh",
            )

    def test_saved_gateway_cookies_are_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gateway-cookies.txt").write_text("PLATFORM_SESSION=saved", encoding="utf-8")
            cfg = ClientConfig(auth_mode="prod", session_cookies={"PLATFORM_SESSION": "ift-session"})
            inst = HeadlessInstance(work_dir=tmp, cfg=cfg, log_dir=root)
            inst._load_gateway_cookies()

        self.assertEqual(inst.cfg.gateway_cookies, {"PLATFORM_SESSION": "saved"})
        self.assertEqual(inst.cfg.session_cookies, {"PLATFORM_SESSION": "ift-session"})


if __name__ == "__main__":
    unittest.main()
