"""Тесты сценария presentation_clone и проверок, которым не нужны фикстуры.

Кейсы, требующие внешних презентаций, не переносились: фикстуры
машинно-зависимы и в репозитории не лежат.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from example.cases import artifact_specs, available_scenarios, build_check_list, load_test_cases
from example.checks.functional import run_functional_check
from example.checks.llm import get_llm_profile
from example.models import Artifact, ArtifactContext, Check

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
TEMPLATE_MEDIA_HANDLER = "pptx/raw_structure/template_media"
CASE_COUNTS = {
    "US01-01-01": 55,
    "US01-01-02": 57,
    "US01-01-03": 58,
    "US01-01-04": 56,
    "US01-01-05": 56,
    "US01-01-06": 56,
    "US01-01-07": 57,
    "US01-01-08": 57,
}
INSTRUCTION_COUNTS = {
    "US01-01-01": 4,
    "US01-01-02": 5,
    "US01-01-03": 7,
    "US01-01-04": 5,
    "US01-01-05": 5,
    "US01-01-06": 5,
    "US01-01-07": 6,
    "US01-01-08": 5,
}
CASE_ASSETS = {
    "US01-01-01": "AI_Technology_Slidesgo_onestyle.pptx",
    "US01-01-02": "AI_Technology_Slidesgo_onestyle.pptx",
    "US01-01-03": "AI_Technology_Slidesgo_onestyle.pptx",
    "US01-01-04": "Remote_Working_Slidesgo_onestyle.pptx",
    "US01-01-05": "Remote_Working_Slidesgo_onestyle.pptx",
    "US01-01-06": "Remote_Working_Slidesgo_onestyle.pptx",
    "US01-01-07": "Северный_ветер.pptx",
    "US01-01-08": "Северный_ветер.pptx",
}


def functional_check(handler: str, artifacts: list[str], options: dict | None = None) -> Check:
    return Check(
        text="",
        kind="function",
        handler=handler,
        artifacts=artifacts,
        options=options or {},
    )


def run_media_check(template_files: dict[str, bytes], final_files: dict[str, bytes]):
    with tempfile.TemporaryDirectory() as directory:
        template = Path(directory) / "template.pptx"
        final = Path(directory) / "presentation.pptx"
        for path, files in ((template, template_files), (final, final_files)):
            with ZipFile(path, "w") as archive:
                for name, content in files.items():
                    archive.writestr(f"ppt/media/{name}", content)
        context = ArtifactContext(
            items=[
                Artifact(path=str(template), role="source", name="template", artifact_type=".pptx"),
                Artifact(path=str(final), role="final", name="presentation", artifact_type=".pptx"),
            ],
            processed=True,
        )
        check = functional_check(TEMPLATE_MEDIA_HANDLER, ["template", "presentation"])
        check.use_user_phrase = False
        return run_functional_check(check, context)


class PresentationCloneTests(unittest.TestCase):
    def test_scenario_cases_use_fully_qualified_groups(self):
        cases = load_test_cases(scenario="presentation_clone")
        self.assertEqual([case.id for case in cases], list(CASE_COUNTS))
        self.assertIn("presentation_clone", available_scenarios())
        for case in cases:
            with self.subTest(case=case.id):
                checks = build_check_list(case)
                self.assertEqual(len(checks), CASE_COUNTS[case.id])
                self.assertTrue(all("/" in group for group in case.metric_groups))
                attachment = Path(case.case_dir, case.attachments[0].path)
                self.assertEqual(attachment.name, CASE_ASSETS[case.id])
                self.assertTrue(attachment.is_file())
                self.assertEqual(
                    [spec.name for spec in artifact_specs(case)],
                    ["style_reference", "presentation"],
                )
                functions = [check for check in checks if check.kind == "function"]
                handlers = {check.handler for check in functions}
                expected_m3 = M3_HANDLERS + ((TEMPLATE_MEDIA_HANDLER,) if case.id == "US01-01-08" else ())
                custom = set(M1_HANDLERS + expected_m3 + M5_HANDLERS)
                self.assertTrue(custom.issubset(handlers))
                self.assertTrue(all(not check.text for check in functions if check.handler in custom))
                metric_counts = {
                    "M1. Выбор шаблона": 2,
                    "M3. Сохранение оформления шаблона": len(expected_m3),
                    "M5. Чистота от шаблона": 3,
                }
                for metric, expected in metric_counts.items():
                    self.assertEqual(sum(check.metric == metric for check in functions), expected)
                instruction_checks = [check for check in checks if check.metric == "Следование инструкции"]
                self.assertEqual(len(instruction_checks), INSTRUCTION_COUNTS[case.id])
                self.assertTrue(
                    all(check.use_user_phrase for check in instruction_checks if check.kind == "llm")
                )
                m5_checks = [check for check in functions if check.metric == "M5. Чистота от шаблона"]
                self.assertCountEqual([check.handler for check in m5_checks], M5_HANDLERS)
                for check in m5_checks:
                    self.assertTrue(check.options.get("terms") or check.options.get("patterns"))
                    self.assertNotIn("presentation_clone", str(check.options).casefold())
                    self.assertNotIn("template_cleanup_ai", str(check.options).casefold())
                media_checks = [check for check in functions if check.handler == TEMPLATE_MEDIA_HANDLER]
                self.assertEqual(len(media_checks), 1 if case.id == "US01-01-08" else 0)
                if media_checks:
                    self.assertEqual(media_checks[0].artifacts, ["template", "presentation"])
                    self.assertFalse(media_checks[0].use_user_phrase)

    def test_judge_profile_reuses_presentation_profile(self):
        profile = get_llm_profile("presentation_clone")
        base = importlib.import_module("example.checks.llm.presentation")
        for name in ("SYSTEM_PROMPT", "build_text_prompt", "build_vision_prompt", "JudgeResult"):
            self.assertIs(getattr(profile, name), getattr(base, name))

    def test_style_header_accepts_valid_header_without_pptx(self):
        with tempfile.TemporaryDirectory() as directory:
            style = Path(directory) / "STYLE.md"
            context = ArtifactContext(
                items=[
                    Artifact(
                        path=str(style),
                        role="intermediate",
                        name="style_reference",
                        artifact_type=".md",
                        text="# STYLE.md — Template.pptx — 10 slides\n",
                    )
                ],
                processed=True,
            )
            result = run_functional_check(
                functional_check("text/template_style_header", ["style_reference"]),
                context,
            )
            self.assertTrue(result.success)

    def test_style_header_rejects_invalid_header(self):
        with tempfile.TemporaryDirectory() as directory:
            style = Path(directory) / "STYLE.md"
            context = ArtifactContext(
                items=[
                    Artifact(
                        path=str(style),
                        role="intermediate",
                        name="style_reference",
                        artifact_type=".md",
                        text="# STYLE.md без имени шаблона\n",
                    )
                ],
                processed=True,
            )
            result = run_functional_check(
                functional_check("text/template_style_header", ["style_reference"]),
                context,
            )
            self.assertFalse(result.success)
            self.assertIn("распознаваемого заголовка", result.reason)
            self.assertNotIn("ожидается", result.reason)

    def test_style_reference_rejects_mismatched_template_name(self):
        with tempfile.TemporaryDirectory() as directory:
            style = Path(directory) / "STYLE.md"
            template = Path(directory) / "Expected.pptx"
            context = ArtifactContext(
                items=[
                    Artifact(
                        path=str(style),
                        role="intermediate",
                        name="style_reference",
                        artifact_type=".md",
                        text="# STYLE.md — Other.pptx — 10 slides\n",
                    ),
                    Artifact(path=str(template), role="source", name="template", artifact_type=".pptx"),
                ],
                processed=True,
            )
            result = run_functional_check(
                functional_check("text/template_style_reference", ["style_reference", "template"]),
                context,
            )
            self.assertFalse(result.success)
            self.assertIn("Other.pptx", result.reason)
            self.assertIn("Expected.pptx", result.reason)

    def test_template_media_accepts_same_bytes_under_different_names(self):
        result = run_media_check(
            {"image1.png": b"shared-image"},
            {"renamed.jpg": b"shared-image"},
        )
        self.assertTrue(result.success)

    def test_template_media_rejects_foreign_media(self):
        result = run_media_check(
            {"image1.png": b"shared-image"},
            {"renamed.jpg": b"shared-image", "foreign.png": b"foreign-image"},
        )
        self.assertFalse(result.success)
        self.assertIn("ppt/media/foreign.png", result.reason)


if __name__ == "__main__":
    unittest.main()
