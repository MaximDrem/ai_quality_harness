"""Тесты автодiscovery сценариев, YAML-групп, handlers и весов."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from example.cases import available_scenarios, build_check_list, load_test_cases
from example.checks.functional import _load_handler, clear_handler_cache, run_functional_check
from example.evaluate import case_for_validate
from example.models import Artifact, ArtifactContext, Check
from example.run_test_agent import _parse_args, model_slug


class ScenarioTests(unittest.TestCase):
    def test_presentations_keep_existing_checklists(self):
        expected = {
            "GD-001": 45,
            "GD-004": 45,
            "GD-007": 45,
            "GD-010": 45,
            "GD-013": 45,
            "GD-016": 44,
            "GD-019": 45,
            "GD-022": 44,
            "GD-025": 45,
            "GD-026": 44,
        }
        cases = load_test_cases()
        self.assertEqual(sum(expected.values()), 447)
        for case in cases:
            checks = build_check_list(case)
            self.assertEqual(len(checks), expected[case.id])
            for check in checks:
                if check.kind == "function":
                    self.assertTrue(callable(_load_handler(check.handler or check.text.split()[0])))
                else:
                    self.assertEqual(check.kind, "llm")
                    self.assertIn(check.input, {"text", "raw_structure", "rendered_photos", "tool_call"})

    def test_scenarios_are_discovered_from_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "z_skill").mkdir()
            (root / "z_skill" / "case.yaml").write_text("id: one\n", encoding="utf-8")
            (root / "a_skill").mkdir()
            (root / "a_skill" / "case.yaml").write_text("id: two\n", encoding="utf-8")
            (root / "empty").mkdir()
            self.assertEqual(available_scenarios(root), ["a_skill", "z_skill"])

    def test_cases_select_only_declared_metric_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data" / "demo"
            metrics_dir = root / "metrics" / "demo"
            data_dir.mkdir(parents=True)
            metrics_dir.mkdir(parents=True)
            (metrics_dir / "layout.yaml").write_text(
                "phase: universal\nweight: 2\nchecks:\n- kind: llm\n  input: text\n  metric: Layout\n  text: selected\n",
                encoding="utf-8",
            )
            (metrics_dir / "style.yaml").write_text(
                "phase: universal\nchecks:\n- kind: llm\n  input: text\n  metric: Style\n  text: not selected\n",
                encoding="utf-8",
            )
            (data_dir / "one.yaml").write_text(
                "id: one\nartifact_type: .pptx\nmetric_groups: [layout]\nchecks: []\n",
                encoding="utf-8",
            )
            (data_dir / "two.yaml").write_text(
                "id: two\nartifact_type: .pptx\nchecks:\n- kind: llm\n  input: text\n  text: own\n  metric: Standalone\n  weight: 0.5\n",
                encoding="utf-8",
            )
            with patch("example.cases.METRICS_DIR", root / "metrics"):
                cases = load_test_cases(cases_dir=str(data_dir), scenario="demo")
                selected = build_check_list(cases[0])
                standalone = build_check_list(cases[1])
                self.assertEqual([check.text for check in selected], ["selected"])
                self.assertEqual(selected[0].weight, 2)
                self.assertEqual([check.text for check in standalone], ["own"])
                self.assertEqual(standalone[0].weight, 0.5)

    def test_validation_respects_case_and_default_groups(self):
        with_case = case_for_validate("deck.pptx", "GD-001", None)
        default = case_for_validate("deck.pptx", None, None)
        self.assertEqual(len(with_case.check_list), 45)
        self.assertEqual(len(default.check_list), 37)

    def test_functional_handler_is_discovered_without_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "text").mkdir()
            (root / "text" / "echo.py").write_text(
                "from example.models import CheckResult\n"
                "def run(check, artifacts, user_phrase=None, context=None):\n"
                "    return CheckResult(check=check, success=True, reason=check.options['value'])\n",
                encoding="utf-8",
            )
            clear_handler_cache()
            with patch("example.checks.functional._ROOT", root):
                clear_handler_cache()
                check = Check(
                    text="custom check",
                    kind="function",
                    handler="text/echo",
                    options={"value": "loaded"},
                )
                artifact = Artifact(path=None, role="final", kind="message", text="text")
                result = run_functional_check(check, ArtifactContext(items=[artifact]))
                self.assertTrue(result.success)
                self.assertEqual(result.reason, "loaded")
                self.assertEqual(len(artifact.results), 1)
            clear_handler_cache()

    def test_llm_requires_explicit_input_and_old_kinds_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "missing.yaml").write_text(
                "id: missing\nartifact_type: .md\nchecks:\n- kind: llm\n  text: no input\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "input обязателен"):
                load_test_cases(cases_dir=str(root))
            (root / "missing.yaml").write_text(
                "id: old\nartifact_type: .md\nchecks:\n- kind: text\n  text: old kind\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "kind должен быть llm или function"):
                load_test_cases(cases_dir=str(root))

    def test_cli_auth_skills_and_model_slug(self):
        args = _parse_args(["--auth-mode", "prod", "--skills-dir", "/tmp/config", "--scenario", "sample_scenario"])
        self.assertEqual(args.auth_mode, "prod")
        self.assertEqual(args.skills_dir, "/tmp/config")
        self.assertEqual(args.scenario, "sample_scenario")
        self.assertEqual(model_slug("gigachat/deepseek-v4.1"), "deepseek_v4.1")
        self.assertEqual(model_slug("provider/a/b"), "b")


if __name__ == "__main__":
    unittest.main()
