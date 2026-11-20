"""Тесты динамических eager-процессоров и повторного использования проверок."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from example.cases import build_check_list, load_test_cases
from example.checks.functional import clear_handler_cache, run_functional_check, run_functional_checks
from example.models import Artifact, ArtifactContext, Check
from example.processors import clear_processor_cache, get_processor, process_context


def processed_context(artifact: Artifact) -> ArtifactContext:
    context = ArtifactContext(items=[artifact])
    process_context(context)
    return context


class ProcessorTests(unittest.TestCase):
    def tearDown(self):
        clear_handler_cache()
        clear_processor_cache()

    def test_common_text_check_uses_text_processor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            check = Check(text="repeated", kind="function", handler="text/repeated_words")
            for content, expected in (
                ("normal text", True),
                ("word word", False),
                ("first word\nword next", True),
            ):
                path.write_text(content, encoding="utf-8")
                artifact = Artifact(path=str(path), role="final", name="final")
                context = processed_context(artifact)
                self.assertEqual(run_functional_check(check, context).success, expected)

    def test_same_text_check_works_with_pptx_processor(self):
        from pptx import Presentation

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deck.pptx"
            presentation = Presentation()
            slide = presentation.slides.add_slide(presentation.slide_layouts[6])
            slide.shapes.add_textbox(0, 0, 1000000, 1000000).text = "word word"
            presentation.save(path)
            artifact = Artifact(path=str(path), role="final", name="final")
            context = processed_context(artifact)
            check = Check(text="repeated", kind="function", handler="text/repeated_words")
            result = run_functional_check(check, context)
            self.assertFalse(result.success)
            self.assertIn("word word", result.reason)
            raw_check = Check(
                text="slide_count",
                kind="function",
                handler="pptx/raw_structure/slide_count",
                options={"condition": 1},
            )
            raw_result = run_functional_check(raw_check, context)
            self.assertTrue(raw_result.success)
            self.assertEqual(len(artifact.raw_structure.slides), 1)
            self.assertIn('"slides"', artifact.serialized_raw_structure)

    def test_corrupt_artifact_becomes_failed_check_not_case_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.pptx"
            path.write_bytes(b"not a pptx")
            artifact = Artifact(path=str(path), role="final", name="final")
            context = processed_context(artifact)
            check = Check(
                text="slide_count",
                kind="function",
                handler="pptx/raw_structure/slide_count",
                options={"condition": 1},
            )
            result = run_functional_check(check, context)
            self.assertFalse(result.success)
            self.assertIn("недоступна", result.reason)
            self.assertIn("raw_structure", artifact.processing_errors)

    def test_eager_extraction_uses_artifact_processor(self):
        artifact = Artifact(
            path="/tmp/result.custom",
            role="final",
            name="final",
            processor="custom",
        )
        context = ArtifactContext(items=[artifact])

        class CustomProcessor:
            def process(self, item):
                item.text = "artifact text"
                item.raw_structure = {"text": item.text}
                item.serialized_raw_structure = '{"text": "artifact text"}'
                item.rendered_photos = []

        with patch("example.checks.processors.get_processor", return_value=CustomProcessor()) as factory:
            process_context(context)
        self.assertEqual(artifact.text, "artifact text")
        self.assertTrue(context.processed)
        self.assertEqual(factory.call_args.kwargs["name"], "custom")

    def test_new_scenario_needs_only_declarative_files_and_domain_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data" / "sample_scenario"
            metrics_dir = root / "metrics" / "sample_scenario"
            functional_dir = root / "functional"
            processors_dir = root / "processors"
            data_dir.mkdir(parents=True)
            metrics_dir.mkdir(parents=True)
            (functional_dir / "text").mkdir(parents=True)
            processors_dir.mkdir()
            (data_dir / "case.yaml").write_text(
                "id: sample\nuser_phrase: test\nartifact_type: .custom\n"
                "processor: demo\nmetric_groups: [sample_scenario/quality]\nchecks: []\n",
                encoding="utf-8",
            )
            (metrics_dir / "quality.yaml").write_text(
                "phase: universal\nweight: 2\nchecks:\n"
                "- kind: function\n  handler: text/check_content\n  text: custom content\n  metric: Custom\n",
                encoding="utf-8",
            )
            (processors_dir / "processor_demo.py").write_text(
                "from pathlib import Path\n"
                "class Processor:\n"
                "    extensions = {'.custom'}\n"
                "    def process(self, artifact):\n"
                "        artifact.text = Path(artifact.path).read_text(encoding='utf-8')\n"
                "        artifact.raw_structure = {'text': artifact.text}\n"
                "        artifact.serialized_raw_structure = '{}'\n"
                "        artifact.rendered_photos = []\n",
                encoding="utf-8",
            )
            (functional_dir / "text" / "check_content.py").write_text(
                "from example.checks._helpers import ok, unavailable\n"
                "def run(check, artifacts, user_phrase=None, context=None):\n"
                "    if artifacts[0].text == 'valid':\n"
                "        return ok(check, 'valid', artifacts, user_phrase)\n"
                "    return unavailable(check, 'invalid', artifacts, user_phrase)\n",
                encoding="utf-8",
            )
            artifact_path = data_dir / "result.custom"
            artifact_path.write_text("valid", encoding="utf-8")
            clear_handler_cache()
            clear_processor_cache()
            with (
                patch("example.cases.METRICS_DIR", root / "metrics"),
                patch("example.checks.functional._ROOT", functional_dir),
                patch("example.checks.processors._ROOT", processors_dir),
            ):
                case = load_test_cases(cases_dir=str(data_dir), scenario="sample_scenario")[0]
                case.check_list = build_check_list(case)
                artifact = Artifact(
                    path=str(artifact_path),
                    role="final",
                    name="final",
                    processor=case.processor,
                )
                context = processed_context(artifact)
                results = run_functional_checks(case, context)
                self.assertEqual(len(results), 1)
                self.assertTrue(results[0].success)
                self.assertEqual(results[0].check.weight, 2)
                self.assertEqual(len(artifact.results), 1)
                self.assertEqual(get_processor(path=str(artifact_path)).__class__.__name__, "Processor")


if __name__ == "__main__":
    unittest.main()
