"""OPENCODE_SELF_PROTECTION: набор путей и JSON как selfProtectionValue()."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from client.server.self_protection import (
    app_data_dir,
    self_protection,
    self_protection_value,
    user_data_dir,
)


class SelfProtectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.app = self.root / "packages" / "desktop-electron"
        self.base = self.root / ".wsc"

    def test_paths_mirror_desktop(self) -> None:
        value = self_protection(self.app, base=self.base)

        self.assertEqual(
            value["protected"],
            [self.app, self.app / "resources", user_data_dir(), self.base],
        )
        self.assertEqual(
            value["allowed"],
            [
                self.app,
                self.base / "config" / "skills",
                self.base / "data" / "plans",
                self.base / "data" / "tool-output",
                self.base / "data" / "uploads",
                self.base / "data" / "worktree",
            ],
        )
        self.assertEqual(value["readOnly"], [self.base / "data" / "log"])

    def test_packaged_build_hides_app_path(self) -> None:
        value = self_protection(self.app, base=self.base, packaged=True)

        self.assertNotIn(self.app, value["allowed"])
        self.assertIn(self.app, value["protected"])

    def test_config_dir_follows_opencode_config_dir(self) -> None:
        config = self.root / "config-dir"

        value = self_protection(self.app, base=self.base, config_dir=config)

        self.assertIn(config / "skills", value["allowed"])

    def test_user_data_is_per_channel(self) -> None:
        self.assertEqual(user_data_dir("dev").parent, app_data_dir())
        self.assertEqual(user_data_dir("dev").name, "wsc.dev")
        self.assertEqual(user_data_dir("beta").name, "wsc.beta")
        self.assertEqual(user_data_dir("prod").name, "wsc")

    def test_value_is_json_with_all_sections(self) -> None:
        raw = json.loads(self_protection_value(self.app, base=self.base))

        self.assertEqual(sorted(raw), ["allowed", "protected", "readOnly"])
        self.assertTrue(raw["protected"])
        self.assertTrue(all(isinstance(item, str) for item in raw["protected"]))


if __name__ == "__main__":
    unittest.main()
