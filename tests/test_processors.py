from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from example.cases import build_check_list, load_test_cases
from example.checks.functional import clear_handler_cache, run_functional_checks
from example.checks.functional.text.repeated_words import run as repeated_words
from example.judge.extract import parse_artifacts_text
from example.models import ArtifactContext, Check, CollectedArtifact
from example.processors import clear_processor_cache, get_processor


class ProcessorTests(unittest.TestCase):
    def tearDown(self):
        clear_handler_cache()
        clear_processor_cache()

    def test_common_text_check_uses_text_processor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            path.write_text("normal text", encoding="utf-8")
            context = ArtifactContext(
                items=[CollectedArtifact(path=str(path), role="final", name="final", processor="text")]
            )
            check = Check(text="repeated", kind="functional", processor="text")
            self.assertTrue(repeated_words(check, [str(path)], [], context=context).success)
            path.write_text("word word", encoding="utf-8")
            self.assertFalse(repeated_words(check, [str(path)], [], context=context).success)
            path.write_text("first word\nword next", encoding="utf-8")
            self.assertTrue(repeated_words(check, [str(path)], [], context=context).success)

    def test_same_text_check_works_with_pptx_processor(self):
        from pptx import Presentation

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deck.pptx"
            presentation = Presentation()
            slide = presentation.slides.add_slide(presentation.slide_layouts[6])
            slide.shapes.add_textbox(0, 0, 1000000, 1000000).text = "word word"
            presentation.save(path)
            context = ArtifactContext(
                items=[CollectedArtifact(path=str(path), role="final", name="final", processor="pptx")]
            )
            check = Check(text="repeated", kind="functional", processor="pptx")
            result = repeated_words(check, [str(path)], [], context=context)
            self.assertFalse(result.success)
            self.assertIn("word word", result.reason)

    def test_judge_extraction_uses_artifact_processor(self):
        artifact = CollectedArtifact(
            path="/tmp/result.custom",
            role="final",
            name="final",
            processor="artifact_processor",
        )
        context = ArtifactContext(items=[artifact])
        processor = type("Processor", (), {"extract_text": lambda self, path: "artifact text"})()
        with patch("example.processors.get_processor", return_value=processor) as factory:
            text = parse_artifacts_text([artifact.path], context=context)
        self.assertIn("artifact text", text)
        self.assertEqual(factory.call_args.kwargs["name"], "artifact_processor")

    def test_new_scenario_needs_only_declarative_files_and_domain_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data" / "demo"
            metrics_dir = root / "metrics" / "demo"
            functional_dir = root / "functional" / "demo"
            data_dir.mkdir(parents=True)
            metrics_dir.mkdir(parents=True)
            functional_dir.mkdir(parents=True)
            (data_dir / "case.yaml").write_text(
                "id: demo\nuser_phrase: test\nartifact_type: .custom\n"
                "processor: demo\nmetric_groups: [quality]\nchecks: []\n",
                encoding="utf-8",
            )
            (metrics_dir / "quality.yaml").write_text(
                "processor: demo\nphase: universal\nchecks:\n"
                "- kind: functional\n  handler: demo/check_content\n  text: custom content\n",
                encoding="utf-8",
            )
            (functional_dir / "processor.py").write_text(
                "from pathlib import Path\n"
                "class Processor:\n"
                "    extensions = {'.custom'}\n"
                "    def extract_text(self, path):\n"
                "        return Path(path).read_text(encoding='utf-8')\n"
                "    def render(self, path, output_dir):\n"
                "        return []\n",
                encoding="utf-8",
            )
            (functional_dir / "check_content.py").write_text(
                "from example.checks.functional._helpers import ok, unavailable\n"
                "def run(check, artifacts, photos, user_phrase=None, context=None):\n"
                "    text = context.extract_text(artifacts[0], check.processor)\n"
                "    if text == 'valid':\n"
                "        return ok(check, 'valid', artifacts, photos, user_phrase)\n"
                "    return unavailable(check, 'invalid', artifacts, photos, user_phrase)\n",
                encoding="utf-8",
            )
            artifact = data_dir / "result.custom"
            artifact.write_text("valid", encoding="utf-8")
            clear_handler_cache()
            clear_processor_cache()
            with (
                patch("example.cases.METRICS_DIR", root / "metrics"),
                patch("example.checks.functional._ROOT", functional_dir.parent),
                patch("example.processors._ROOT", functional_dir.parent),
            ):
                case = load_test_cases(cases_dir=str(data_dir), scenario="demo")[0]
                case.check_list = build_check_list(case)
                context = ArtifactContext(
                    items=[
                        CollectedArtifact(
                            path=str(artifact),
                            role="final",
                            name="final",
                            processor=case.processor,
                        )
                    ]
                )
                results = run_functional_checks(case, [str(artifact)], [], context=context)
                self.assertEqual(len(results), 1)
                self.assertTrue(results[0].success)
                self.assertEqual(get_processor(path=str(artifact)).extract_text(str(artifact)), "valid")


if __name__ == "__main__":
    unittest.main()
