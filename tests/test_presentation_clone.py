"""Тесты сценария presentation_clone и проверок, которым не нужны фикстуры.

Кейсы, требующие внешних презентаций, не переносились: фикстуры
машинно-зависимы и в репозитории не лежат.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path

from example.cases import artifact_specs, available_scenarios, build_check_list, load_test_cases
from example.checks.functional import run_functional_check
from example.checks.llm import get_llm_profile
from example.models import Artifact, ArtifactContext, Check
from example.processors import process_context

M1_HANDLERS = (
    "text/template_style_header",
    "text/template_style_reference",
)
M3_HANDLERS = (
    "pptx/raw_structure/template_slide_size",
    "pptx/raw_structure/template_master_theme",
    "pptx/raw_structure/template_layouts",
    "pptx/raw_structure/template_used_layouts",
    "pptx/raw_structure/template_fonts",
    "pptx/raw_structure/template_text_colors",
)
M5_HANDLERS = (
    "pptx/raw_structure/forbidden_slide_text",
    "pptx/raw_structure/forbidden_metadata",
    "pptx/raw_structure/forbidden_links",
)


def functional_check(handler: str, artifacts: list[str], options: dict | None = None) -> Check:
    return Check(
        text="",
        kind="function",
        handler=handler,
        artifacts=artifacts,
        options=options or {},
    )


class PresentationCloneTests(unittest.TestCase):
    def test_scenario_cases_use_fully_qualified_groups(self):
        cases = load_test_cases(scenario="presentation_clone")
        self.assertEqual([case.id for case in cases], ["US01-01-01", "US01-01-02", "US01-01-03"])
        self.assertIn("presentation_clone", available_scenarios())
        for case in cases:
            checks = build_check_list(case)
            self.assertEqual(len(checks), 50)
            self.assertTrue(all("/" in group for group in case.metric_groups))
            self.assertTrue(Path(case.case_dir, case.attachments[0].path).is_file())
            self.assertEqual(
                [spec.name for spec in artifact_specs(case)],
                ["style_reference", "presentation"],
            )
            functions = [check for check in checks if check.kind == "function"]
            handlers = {check.handler for check in functions}
            custom = set(M1_HANDLERS + M3_HANDLERS + M5_HANDLERS)
            self.assertTrue(custom.issubset(handlers))
            self.assertTrue(all(not check.text for check in functions if check.handler in custom))
            metric_counts = {
                "M1. Выбор шаблона": 2,
                "M3. Сохранение оформления шаблона": 6,
                "M5. Чистота от шаблона": 3,
            }
            for metric, expected in metric_counts.items():
                self.assertEqual(sum(check.metric == metric for check in functions), expected)

    def test_judge_profile_reuses_presentation_profile(self):
        profile = get_llm_profile("presentation_clone")
        base = importlib.import_module("example.checks.llm.presentation")
        for name in ("SYSTEM_PROMPT", "build_text_prompt", "build_vision_prompt", "JudgeResult"):
            self.assertIs(getattr(profile, name), getattr(base, name))

    def test_style_header_rejects_invalid_header(self):
        with tempfile.TemporaryDirectory() as directory:
            style = Path(directory) / "STYLE.md"
            style.write_text("# STYLE.md без имени шаблона\n", encoding="utf-8")
            context = ArtifactContext(
                items=[Artifact(path=str(style), role="supporting", name="style_reference", artifact_type=".md")]
            )
            process_context(context)
            result = run_functional_check(
                functional_check("text/template_style_header", ["style_reference"]),
                context,
            )
            self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
