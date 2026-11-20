"""Offline-тесты выбора финальных и вспомогательных артефактов."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from example.artifacts import ArtifactCollector, validation_context
from example.evaluate import _missing_artifact_results
from example.checks.functional.rendered_photos.file_renderable import run as file_renderable
from example.models import ArtifactContext, ArtifactSpec, AttachmentSpec, Check, CollectedArtifact, TestCase
from example.processors import process_context


class ArtifactTests(unittest.TestCase):
    def test_supporting_and_final_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            source = root / "source.pptx"
            source.write_bytes(b"source")
            supporting = work / "analysis.md"
            final = work / "result.pptx"
            case = TestCase(
                id="style",
                user_phrase="test",
                skill="sample_scenario",
                artifact_type=".pptx",
                case_dir=str(root),
                attachments=[AttachmentSpec(path="source.pptx", name="template")],
                artifacts=[
                    ArtifactSpec(name="analysis", artifact_type=".md"),
                    ArtifactSpec(name="result", artifact_type=".pptx", is_final_result=True),
                ],
            )
            collector = ArtifactCollector(case, work)
            result = SimpleNamespace(artifacts=[])

            supporting.write_text("latest version", encoding="utf-8")
            self.assertFalse(collector.has_final(result, []))
            final.write_bytes(b"final")
            self.assertTrue(collector.has_final(result, []))

            context = collector.finalize(result, [])
            selected = context.select(["analysis"])
            self.assertEqual(len(selected), 1)
            self.assertEqual(Path(selected[0].path).read_text(encoding="utf-8"), "latest version")
            self.assertEqual(context.paths(["template"]), [str(source)])
            self.assertEqual(context.paths(["final"]), [str(final)])
            self.assertEqual(context.paths(["supporting"]), [selected[0].path])

    def test_required_artifact_has_explicit_failure(self):
        case = TestCase(
            id="research",
            user_phrase="test",
            skill="sample_scenario",
            artifact_type=".md",
        )
        collector = ArtifactCollector(case, Path("/tmp"))
        context = collector.finalize(SimpleNamespace(artifacts=[]), [])
        missing = _missing_artifact_results(case, context)
        self.assertEqual(len(missing), 1)
        self.assertIn("final", missing[0].reason)

    def test_validation_context_and_plain_text_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pptx"
            source.write_bytes(b"source")
            result = root / "result.md"
            result.write_text("# Research\nText", encoding="utf-8")
            case = TestCase(
                id="research",
                user_phrase="test",
                skill="sample_scenario",
                artifact_type=".md",
                case_dir=str(root),
                attachments=[AttachmentSpec(path="source.pptx", name="source")],
            )
            context = validation_context(case, str(result))
            self.assertEqual(context.paths(["source"]), [str(source)])
            self.assertEqual(context.paths(["final"]), [str(result)])
            process_context(context)
            self.assertIn("# Research", context.select(["final"])[0].text)


    def test_all_required_finals_are_needed(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            case = TestCase(
                id="multi",
                user_phrase="test",
                skill="demo",
                artifact_type=".pptx",
                artifacts=[
                    ArtifactSpec(name="report", artifact_type=".md", is_final_result=True),
                    ArtifactSpec(name="deck", artifact_type=".pptx", is_final_result=True),
                ],
            )
            collector = ArtifactCollector(case, work)
            result = SimpleNamespace(artifacts=[])
            (work / "report.md").write_text("report", encoding="utf-8")
            self.assertFalse(collector.has_final(result, []))
            (work / "deck.pptx").write_bytes(b"deck")
            self.assertTrue(collector.has_final(result, []))

    def test_same_basename_supporting_artifacts_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "a").mkdir()
            (work / "b").mkdir()
            first = work / "a" / "report.md"
            second = work / "b" / "report.md"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            case = TestCase(
                id="supporting",
                user_phrase="test",
                skill="demo",
                artifact_type=".md",
                artifacts=[ArtifactSpec(name="report", artifact_type=".md")],
            )
            collector = ArtifactCollector(case, work)
            context = collector.finalize(SimpleNamespace(artifacts=[], events=[]), [])
            items = context.select(["report"])
            self.assertEqual(len(items), 2)
            self.assertEqual(
                sorted(Path(item.path).read_text(encoding="utf-8") for item in items),
                ["first", "second"],
            )
            self.assertEqual(len({item.path for item in items}), 2)

    def test_ambiguous_final_declaration_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            path = work / "result.pptx"
            path.write_bytes(b"deck")
            case = TestCase(
                id="ambiguous",
                user_phrase="test",
                skill="demo",
                artifact_type=".pptx",
                artifacts=[
                    ArtifactSpec(name="generic", artifact_type=".pptx", is_final_result=True),
                    ArtifactSpec(name="specific", artifact_type=".pptx", path="result.pptx", is_final_result=True),
                ],
            )
            collector = ArtifactCollector(case, work)
            with self.assertRaisesRegex(ValueError, "одновременно"):
                collector.finalize(SimpleNamespace(artifacts=[]), [])

    def test_glob_only_custom_extension_is_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            path = work / "result.yaml"
            path.write_text("result", encoding="utf-8")
            case = TestCase(
                id="custom",
                user_phrase="test",
                skill="demo",
                artifact_type="",
                processor="text",
                artifacts=[ArtifactSpec(name="config", path="result.yaml", is_final_result=True)],
            )
            collector = ArtifactCollector(case, work)
            context = collector.finalize(SimpleNamespace(artifacts=[]), [])
            self.assertEqual(context.paths(["config"]), [str(path)])
            self.assertEqual(context.select(["config"])[0].processor, "text")

    def test_validation_requires_existing_matching_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = TestCase(
                id="multi",
                user_phrase="test",
                skill="demo",
                artifact_type=".pptx",
                artifacts=[
                    ArtifactSpec(name="first", artifact_type=".pptx", path="first.pptx", is_final_result=True),
                    ArtifactSpec(name="second", artifact_type=".pptx", path="second.pptx", is_final_result=True),
                ],
            )
            with self.assertRaises(FileNotFoundError):
                validation_context(case, str(root / "missing.pptx"))
            second = root / "second.pptx"
            second.write_bytes(b"second")
            context = validation_context(case, str(second))
            self.assertEqual(context.paths(["second"]), [str(second)])
            self.assertEqual(context.paths(["first"]), [])

    def test_custom_supporting_is_found_by_filesystem_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            result = work / "analysis.custom"
            result.write_text("analysis", encoding="utf-8")
            case = TestCase(
                id="custom",
                user_phrase="test",
                skill="demo",
                artifact_type="",
                artifacts=[ArtifactSpec(name="analysis", path="analysis.custom")],
            )
            collector = ArtifactCollector(case, work)
            context = collector.finalize(SimpleNamespace(artifacts=[], events=[]), [])
            items = context.select(["analysis"])
            self.assertEqual(len(items), 1)
            self.assertEqual(Path(items[0].path).read_text(encoding="utf-8"), "analysis")

    def test_validation_supports_nested_declared_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "output" / "result.md"
            result.parent.mkdir()
            result.write_text("result", encoding="utf-8")
            case = TestCase(
                id="nested",
                user_phrase="test",
                skill="demo",
                artifact_type="",
                artifacts=[ArtifactSpec(name="report", path="output/result.md", is_final_result=True)],
            )
            context = validation_context(case, str(result))
            self.assertEqual(context.paths(["report"]), [str(result)])

    def test_standard_extension_is_ignored_when_not_declared(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            undeclared = work / "result.md"
            undeclared.write_text("markdown", encoding="utf-8")
            case = TestCase(id="pptx-only", user_phrase="test", skill="demo", artifact_type=".pptx")
            collector = ArtifactCollector(case, work)
            result = SimpleNamespace(artifacts=[], events=[])
            self.assertFalse(collector.has_final(result, []))
            context = collector.finalize(result, [])
            self.assertEqual(context.paths(["final"]), [])

    def test_uploads_are_never_final_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            uploaded = work / "uploads" / "session" / "message" / "source.pptx"
            uploaded.parent.mkdir(parents=True)
            uploaded.write_bytes(b"source")
            case = TestCase(id="upload", user_phrase="test", skill="demo", artifact_type=".pptx")
            collector = ArtifactCollector(case, work)
            result = SimpleNamespace(artifacts=[], events=[])
            self.assertFalse(collector.has_final(result, []))
            final = work / "nested" / "result.pptx"
            final.parent.mkdir()
            final.write_bytes(b"final")
            self.assertTrue(collector.has_final(result, []))
            context = collector.finalize(result, [])
            self.assertEqual(context.paths(["final"]), [str(final)])

    def test_final_message_path_has_priority_over_filesystem(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            first = work / "first.pptx"
            second = work / "nested" / "second.pptx"
            second.parent.mkdir()
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            part = SimpleNamespace(
                type="message.part.updated",
                properties={"part": {"type": "text", "messageID": "message", "text": f"Готово: {second}"}},
            )
            updated = SimpleNamespace(
                type="message.updated",
                properties={"info": {"id": "message", "role": "assistant", "finish": "stop", "time": {"completed": 1}}},
            )
            idle = SimpleNamespace(type="session.status", properties={"status": {"type": "idle"}})
            result = SimpleNamespace(artifacts=[], events=[part, updated, idle])
            case = TestCase(id="priority", user_phrase="test", skill="demo", artifact_type=".pptx")
            collector = ArtifactCollector(case, work)
            context = collector.finalize(result, [])
            self.assertEqual(context.paths(["final"]), [str(second)])
            self.assertIn(str(second), context.select(["final_message"])[0].text)

    def test_final_message_can_be_the_only_required_artifact(self):
        part = SimpleNamespace(
            type="message.part.updated",
            properties={"part": {"type": "text", "messageID": "message", "text": "Ответ пользователю"}},
        )
        updated = SimpleNamespace(
            type="message.updated",
            properties={"info": {"id": "message", "role": "assistant", "finish": "stop", "time": {"completed": 1}}},
        )
        idle = SimpleNamespace(type="session.status", properties={"status": {"type": "idle"}})
        result = SimpleNamespace(artifacts=[], events=[part, updated, idle])
        case = TestCase(
            id="message",
            user_phrase="test",
            skill="demo",
            artifact_type="",
            artifacts=[ArtifactSpec(name="final_message", kind="message")],
        )
        collector = ArtifactCollector(case, Path("/tmp"))
        self.assertTrue(collector.has_final(result, []))
        context = collector.finalize(result, [])
        self.assertEqual(context.select(["final_message"])[0].text, "Ответ пользователю")

    def test_message_only_case_does_not_extract_existing_file_path(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            existing = work / "node.js"
            existing.write_text("console.log(1)", encoding="utf-8")
            part = SimpleNamespace(
                type="message.part.updated",
                properties={"part": {"type": "text", "messageID": "message", "text": f"Ответ готов: {existing}"}},
            )
            updated = SimpleNamespace(
                type="message.updated",
                properties={"info": {"id": "message", "role": "assistant", "finish": "stop", "time": {"completed": 1}}},
            )
            result = SimpleNamespace(artifacts=[], events=[part, updated])
            case = TestCase(
                id="message-only",
                user_phrase="test",
                skill="demo",
                artifact_type="",
                artifacts=[ArtifactSpec(name="final_message", kind="message")],
            )
            collector = ArtifactCollector(case, work)
            self.assertTrue(collector.has_final(result, []))
            context = collector.finalize(result, [])
            self.assertEqual(context.paths(["final"]), [])
            self.assertEqual(context.select(["final_message"])[0].text, f"Ответ готов: {existing}")

    def test_renderable_checks_every_selected_artifact(self):
        first = CollectedArtifact(path="/tmp/first.pptx", role="final", name="first")
        second = CollectedArtifact(path="/tmp/second.pptx", role="final", name="second")
        first.rendered_photos = ["page-1.png"]
        result = file_renderable(Check(text="render", kind="function"), [first, second])
        self.assertFalse(result.success)
        self.assertIn("second.pptx", result.reason)


if __name__ == "__main__":
    unittest.main()
