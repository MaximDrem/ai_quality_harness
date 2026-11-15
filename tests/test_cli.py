from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from example.models import RunResult, TestCase
from example.run_test_agent import main


class CliTests(unittest.TestCase):
    def test_validate_has_no_client_or_cookie_side_effects(self):
        case = TestCase(id="example", user_phrase="", skill="", artifact_type=".md")
        result = RunResult(test_case=case)
        with (
            patch("example.run_test_agent.load_dotenv") as load_env,
            patch("example.run_test_agent.refresh_cookies") as refresh,
            patch("example.run_test_agent._effective_model") as effective_model,
            patch("example.run_test_agent.case_for_validate", return_value=case),
            patch("example.run_test_agent.validate_artifact", AsyncMock(return_value=result)),
            patch("example.run_test_agent.write_results_xlsx", return_value="result.xlsx"),
        ):
            status = asyncio.run(main(["--validate", "report.md"]))
        self.assertEqual(status, 0)
        load_env.assert_called_once()
        refresh.assert_not_called()
        effective_model.assert_not_called()

    def test_full_run_directory_contains_effective_model(self):
        full_run = AsyncMock(return_value=0)
        with (
            patch("example.run_test_agent.load_dotenv"),
            patch("example.run_test_agent.refresh_cookies"),
            patch("example.run_test_agent._effective_model", return_value="gigachat/deepseek-v4.1"),
            patch("example.run_test_agent._full_run_mode", full_run),
        ):
            status = asyncio.run(main(["--runs", "1"]))
        self.assertEqual(status, 0)
        runs_root = full_run.await_args.args[1]
        self.assertRegex(runs_root.name, r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_deepseek_v4\.1$")

    def test_help_exits_before_environment_loading(self):
        with (
            patch("example.run_test_agent.load_dotenv") as load_env,
            patch("example.run_test_agent.refresh_cookies") as refresh,
        ):
            with self.assertRaises(SystemExit) as raised:
                asyncio.run(main(["--help"]))
        self.assertEqual(raised.exception.code, 0)
        load_env.assert_not_called()
        refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
