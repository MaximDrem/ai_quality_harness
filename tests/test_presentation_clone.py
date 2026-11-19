"""Тесты сценария presentation_clone и функциональных проверок шаблона."""

from __future__ import annotations

import importlib
import shutil
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from pptx import Presentation

from example.cases import artifact_specs, available_scenarios, build_check_list, load_test_cases
from example.checks.functional import run_functional_check
from example.checks.llm import get_llm_profile
from example.models import Artifact, ArtifactContext, Check

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "for_tests"
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


def fixture_paths(index: int):
    if index == 1:
        return (
            FIXTURES / "test_1" / "Breakfast Anton V12.pptx",
            FIXTURES / "test_1" / "work_dir" / "chat-helpers" / "remote-security-presentation" / "breakfast-ref" / "STYLE.md",
            FIXTURES / "test_1" / "work_dir" / "remote-security.pptx",
        )
    return (
        FIXTURES / "test_2" / "AI_Technology_Slidesgo.pptx",
        FIXTURES / "test_2" / "work_dir" / "chat-helpers" / "remote-safety-presentation" / "ai-ref" / "STYLE.md",
        FIXTURES / "test_2" / "work_dir" / "remote-safety.pptx",
    )


def context_for(index: int) -> ArtifactContext:
    template, style, final = fixture_paths(index)
    return ArtifactContext(
        items=[
            Artifact(path=str(template), role="source", name="template", artifact_type=".pptx"),
            Artifact(path=str(style), role="intermediate", name="style_reference", artifact_type=".md"),
            Artifact(path=str(final), role="final", name="presentation", artifact_type=".pptx"),
        ]
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

    def test_style_reference_checks_match_attached_template(self):
        for index in (1, 2):
            for handler in M1_HANDLERS:
                result = run_functional_check(
                    functional_check(handler, ["style_reference"] if handler.endswith("header") else ["style_reference", "template"]),
                    context_for(index),
                )
                self.assertTrue(result.success, f"{handler}: {result.reason}")

    def test_style_reference_rejects_other_template(self):
        _, style, _ = fixture_paths(1)
        other, _, _ = fixture_paths(2)
        context = ArtifactContext(
            items=[
                Artifact(path=str(other), role="source", name="template", artifact_type=".pptx"),
                Artifact(path=str(style), role="intermediate", name="style_reference", artifact_type=".md"),
            ]
        )
        result = run_functional_check(
            functional_check("text/template_style_reference", ["style_reference", "template"]),
            context,
        )
        self.assertFalse(result.success)
        self.assertIn("ожидается", result.reason)

    def test_style_header_rejects_invalid_header(self):
        with tempfile.TemporaryDirectory() as directory:
            style = Path(directory) / "STYLE.md"
            style.write_text("# STYLE.md без имени шаблона\n", encoding="utf-8")
            context = ArtifactContext(
                items=[Artifact(path=str(style), role="intermediate", name="style_reference", artifact_type=".md")]
            )
            result = run_functional_check(
                functional_check("text/template_style_header", ["style_reference"]),
                context,
            )
            self.assertFalse(result.success)

    def test_structural_fidelity_checks_accept_fixture_finals(self):
        structural = M3_HANDLERS[:4]
        for index in (1, 2):
            for handler in structural:
                result = run_functional_check(
                    functional_check(handler, ["template", "presentation"]),
                    context_for(index),
                )
                self.assertTrue(result.success, f"{handler}: {result.reason}")

    def test_fixture_text_style_deviations_are_detected(self):
        first_font = run_functional_check(
            functional_check("pptx/raw_structure/template_fonts", ["template", "presentation"]),
            context_for(1),
        )
        second_font = run_functional_check(
            functional_check("pptx/raw_structure/template_fonts", ["template", "presentation"]),
            context_for(2),
        )
        first_colors = run_functional_check(
            functional_check("pptx/raw_structure/template_text_colors", ["template", "presentation"]),
            context_for(1),
        )
        second_colors = run_functional_check(
            functional_check("pptx/raw_structure/template_text_colors", ["template", "presentation"]),
            context_for(2),
        )
        self.assertTrue(first_font.success, first_font.reason)
        self.assertFalse(second_font.success)
        self.assertIn("arial", second_font.reason)
        self.assertFalse(first_colors.success)
        self.assertIn("D9D9D9", first_colors.reason)
        self.assertFalse(second_colors.success)
        self.assertIn("000000", second_colors.reason)

    def test_template_fidelity_checks_reject_foreign_deck(self):
        template, _, _ = fixture_paths(1)
        _, _, final = fixture_paths(2)
        context = ArtifactContext(
            items=[
                Artifact(path=str(template), role="source", name="template", artifact_type=".pptx"),
                Artifact(path=str(final), role="final", name="presentation", artifact_type=".pptx"),
            ]
        )
        failed = []
        for handler in M3_HANDLERS:
            result = run_functional_check(
                functional_check(handler, ["template", "presentation"]),
                context,
            )
            if not result.success:
                failed.append(handler)
        self.assertIn("pptx/raw_structure/template_slide_size", failed)
        self.assertIn("pptx/raw_structure/template_master_theme", failed)
        self.assertIn("pptx/raw_structure/template_layouts", failed)

    def test_template_fonts_check_rejects_foreign_font(self):
        template, _, final = fixture_paths(2)
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "foreign-font.pptx"
            prs = Presentation(str(final))
            for shape in prs.slides[0].shapes:
                if getattr(shape, "has_text_frame", False):
                    paragraphs = shape.text_frame.paragraphs
                    if paragraphs and paragraphs[0].runs:
                        paragraphs[0].runs[0].font.name = "Comic Sans"
                        break
            prs.save(copied)
            context = ArtifactContext(
                items=[
                    Artifact(path=str(template), role="source", name="template", artifact_type=".pptx"),
                    Artifact(path=str(copied), role="final", name="presentation", artifact_type=".pptx"),
                ]
            )
            result = run_functional_check(
                functional_check("pptx/raw_structure/template_fonts", ["template", "presentation"]),
                context,
            )
            self.assertFalse(result.success)
            self.assertIn("Шрифты текста отсутствуют", result.reason)

    def test_forbidden_checks_accept_clean_fixture(self):
        options = {
            "terms": ["Slidesgo", "CREDITS", "Mercury is the closest planet"],
            "patterns": [r"(?i)slidesgo\.com"],
        }
        for handler in M5_HANDLERS:
            result = run_functional_check(
                functional_check(handler, ["presentation"], options),
                context_for(2),
            )
            self.assertTrue(result.success, f"{handler}: {result.reason}")

    def test_forbidden_slide_text_rejects_notes_leftover(self):
        _, _, final = fixture_paths(2)
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "leftover.pptx"
            prs = Presentation(str(final))
            prs.slides[0].notes_slide.notes_text_frame.text = "CREDITS"
            prs.save(copied)
            context = ArtifactContext(
                items=[Artifact(path=str(copied), role="final", name="presentation", artifact_type=".pptx")]
            )
            result = run_functional_check(
                functional_check(
                    "pptx/raw_structure/forbidden_slide_text",
                    ["presentation"],
                    {"terms": ["CREDITS"]},
                ),
                context,
            )
            self.assertFalse(result.success)
            self.assertIn("CREDITS", result.reason)

    def test_forbidden_metadata_and_links_are_independent(self):
        _, _, final = fixture_paths(2)
        options = {"terms": ["Slidesgo"], "patterns": [r"(?i)slidesgo\.com"]}
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "metadata.pptx"
            shutil.copy2(final, metadata)
            with ZipFile(metadata, "a") as archive:
                archive.writestr("docProps/custom.xml", "<custom><trace>Slidesgo</trace></custom>")
            links = Path(directory) / "links.pptx"
            shutil.copy2(final, links)
            with ZipFile(links, "a") as archive:
                archive.writestr(
                    "docProps/_rels/custom.rels",
                    '<Relationship Target="https://slidesgo.com" TargetMode="External"/>',
                )
            for path, handler, phrase in (
                (metadata, "pptx/raw_structure/forbidden_metadata", "метаданных"),
                (links, "pptx/raw_structure/forbidden_links", "ссылки"),
            ):
                context = ArtifactContext(
                    items=[Artifact(path=str(path), role="final", name="presentation", artifact_type=".pptx")]
                )
                result = run_functional_check(
                    functional_check(handler, ["presentation"], options),
                    context,
                )
                self.assertFalse(result.success)
                self.assertIn(phrase, result.reason)


if __name__ == "__main__":
    unittest.main()
