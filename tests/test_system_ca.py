"""Сводный CA-бандл sidecar'а: корни тестовых стендов для голого Node.

Desktop проверяет TLS через Chromium/Electron и на dev-канале игнорирует
ошибки сертификата для корпоративных хостов (`app.ts`, `certificate-error`;
`apiClient.ts`, `rejectUnauthorized: false`). Голый Node так не умеет: листовой
сертификат стенда выпущен внутренним `SberCA Test Ext G2`, которого нет ни в
bundle Mozilla, ни в `/etc/ssl/certs`. Поэтому инстанс собирает бандл из того,
что машина уже считает доверенным (NSS + система + бандл рантайма + env) и
отдаёт его через `NODE_EXTRA_CA_CERTS`. Проверка сертификата при этом остаётся
включённой.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client.server import system_ca
from client.server.server import HeadlessInstance

PEM_A = (
    "-----BEGIN CERTIFICATE-----\n"
    "QUFB\n"
    "-----END CERTIFICATE-----\n"
)
PEM_B = (
    "-----BEGIN CERTIFICATE-----\n"
    "QkJC\n"
    "-----END CERTIFICATE-----\n"
)

# Ники произвольные: в реальном `certutil -L` там `<login>-personal-pki`
# (логин у каждого свой), для разбора важна только колонка Trust Attributes.
CERTUTIL_LISTING = """
Certificate Nickname                                         Trust Attributes
                                                             SSL,S/MIME,JAR/XPI

alpha_root                                                   C,,
sberca-root-ext_sigma                                        CT,c,
user-personal-pki                                            ,,
russian_trusted_sub_ca_pem                                   T,,
web_site_anchor                                              u,u,u
"""


class PemBlocksTest(unittest.TestCase):
    def test_splits_all_blocks(self):
        text = f"# header\n{PEM_A}\n{PEM_B}"
        self.assertEqual(system_ca._pem_blocks(text), [PEM_A, PEM_B])

    def test_ignores_truncated_block(self):
        text = "-----BEGIN CERTIFICATE-----\nQUFB\n"
        self.assertEqual(system_ca._pem_blocks(text), [])

    def test_fingerprint_dedups_same_cert(self):
        self.assertEqual(system_ca._fingerprint(PEM_A), system_ca._fingerprint(PEM_A.replace("QUFB", "QUFB")))
        self.assertNotEqual(system_ca._fingerprint(PEM_A), system_ca._fingerprint(PEM_B))


class TrustedNicknamesTest(unittest.TestCase):
    def test_keeps_only_ca_and_trusted_entries(self):
        # Личный сертификат (` ,, `) и обычный web-анкор (`u,u,u`) в бандл не
        # попадают: доверенными CA они не являются.
        self.assertEqual(
            system_ca.trusted_nicknames(CERTUTIL_LISTING),
            ["alpha_root", "sberca-root-ext_sigma", "russian_trusted_sub_ca_pem"],
        )

    def test_empty_listing(self):
        self.assertEqual(system_ca.trusted_nicknames(""), [])


class BuildBundleTest(unittest.TestCase):
    def test_merges_nss_and_files_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            system_file = root / "system.pem"
            system_file.write_text(PEM_A + PEM_B, encoding="utf-8")
            dest = root / "logs" / "system-ca.pem"
            with mock.patch.object(system_ca, "nss_anchors", return_value=[PEM_A]), mock.patch.object(
                system_ca, "sources", return_value=[system_file]
            ):
                out = system_ca.build_bundle(dest)
            self.assertEqual(out, dest)
            text = dest.read_text(encoding="utf-8")
            self.assertEqual(text.count("BEGIN CERTIFICATE"), 2)
            self.assertIn("QUFB", text)
            self.assertIn("QkJC", text)

    def test_returns_none_without_anchors(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "system-ca.pem"
            with mock.patch.object(system_ca, "nss_anchors", return_value=[]), mock.patch.object(
                system_ca, "sources", return_value=[]
            ):
                self.assertIsNone(system_ca.build_bundle(dest))
            self.assertFalse(dest.exists())

    def test_missing_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "system-ca.pem"
            with mock.patch.object(system_ca, "nss_anchors", return_value=[]), mock.patch.object(
                system_ca, "sources", return_value=[root / "nope.pem"]
            ):
                self.assertIsNone(system_ca.build_bundle(dest))


class SidecarEnvTest(unittest.TestCase):
    def test_bundle_goes_to_node_extra_ca_certs(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = HeadlessInstance(work_dir=tmp, log_dir=Path(tmp) / "logs")
            inst.ca_bundle = Path(tmp) / "logs" / "system-ca.pem"
            env = inst._build_server_env("http://127.0.0.1:1")
        self.assertEqual(env["NODE_EXTRA_CA_CERTS"], str(inst.ca_bundle))

    def test_without_bundle_env_is_untouched(self):
        # Без бандла (инстанс не стартовал) наследуемое окружение не трогаем.
        with tempfile.TemporaryDirectory() as tmp:
            inst = HeadlessInstance(work_dir=tmp, log_dir=Path(tmp) / "logs")
            with mock.patch.dict("os.environ", {"NODE_EXTRA_CA_CERTS": "/tmp/inherited.pem"}):
                env = inst._build_server_env("http://127.0.0.1:1")
        self.assertEqual(env["NODE_EXTRA_CA_CERTS"], "/tmp/inherited.pem")


if __name__ == "__main__":
    unittest.main()
