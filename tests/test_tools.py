"""Offline-тесты Artifact.tool_calls, kind=function/llm и generic LLM input."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class DummyChatOpenAI:
    def __init__(self, *args, **kwargs):
        pass

    def with_structured_output(self, *args, **kwargs):
        return self


sys.modules.setdefault("langchain_openai", SimpleNamespace(ChatOpenAI=DummyChatOpenAI))
sys.modules.setdefault("langchain_core.messages", SimpleNamespace(HumanMessage=object))

from example.artifacts import ArtifactCollector
from example.checks.functional import run_functional_check
from example.judge.runner import run_judge
from example.models import Artifact, ArtifactContext, Check, TestCase
from example.processors import process_context


class ToolTests(unittest.TestCase):
    def test_file_artifact_contains_related_tool_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            result_path = work / "result.md"
            result_path.write_text("result", encoding="utf-8")
            call = SimpleNamespace(
                tool="write",
                state="completed",
                input={"filePath": str(result_path)},
                output="saved successfully",
                error="",
                is_skill=False,
            )
            event = SimpleNamespace(tool_calls=[call], artifacts=[], text_outputs=[])
            result = SimpleNamespace(artifacts=[], events=[event])
            case = TestCase(id="tools", user_phrase="test", skill="demo", artifact_type=".md")
            context = ArtifactCollector(case, work).finalize(result, [])
            artifact = context.select(["final"])[0]
            self.assertEqual(len(artifact.tool_calls), 1)
            self.assertEqual(artifact.tool_calls[0]["output"], "saved successfully")

    def test_functional_tools_check(self):
        artifact = Artifact(
            path=None,
            role="final_message",
            name="final_message",
            kind="message",
            tool_calls=[{"tool": "write", "state": "completed"}],
        )
        check = Check(
            text="write called",
            kind="function",
            handler="tool_calls/tool_called",
            artifacts=["final_message"],
            options={"tool": "write"},
        )
        result = run_functional_check(check, ArtifactContext(items=[artifact], processed=True))
        self.assertTrue(result.success)
        self.assertEqual(result.input_attribute, "tool_calls")

    def test_llm_tools_check(self):
        artifact = Artifact(
            path=None,
            role="final_message",
            name="final_message",
            kind="message",
            tool_calls=[{"tool": "write", "state": "completed", "output": "saved"}],
        )
        check = Check(text="Файл сохранён", kind="llm", input="tool_call", artifacts=["final_message"])
        case = TestCase(id="tools", user_phrase="test", skill="demo", artifact_type="", check_list=[check])
        verdict = SimpleNamespace(success=True, reason="Сохранение подтверждено")
        with patch("example.judge.runner.judge_text", AsyncMock(return_value=verdict)) as judge:
            results = asyncio.run(run_judge(case, ArtifactContext(items=[artifact], processed=True)))
        self.assertTrue(results[0].success)
        self.assertEqual(results[0].input_attribute, "tool_calls")
        self.assertIn("saved", judge.await_args.kwargs["agent_text"])
        self.assertEqual(len(artifact.results), 1)

    def test_llm_raw_structure_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text('{"slides": 2}', encoding="utf-8")
            artifact = Artifact(path=str(path), role="final", name="final")
            check = Check(text="Два слайда", kind="llm", input="raw_structure")
            case = TestCase(id="raw", user_phrase="test", skill="demo", artifact_type=".json", check_list=[check])
            verdict = SimpleNamespace(success=True, reason="Есть два слайда")
            context = ArtifactContext(items=[artifact])
            process_context(context)
            with patch("example.judge.runner.judge_text", AsyncMock(return_value=verdict)) as judge:
                results = asyncio.run(run_judge(case, context))
            self.assertTrue(results[0].success)
            self.assertEqual(results[0].input_attribute, "raw_structure")
            self.assertIn('"slides": 2', judge.await_args.kwargs["agent_text"])


if __name__ == "__main__":
    unittest.main()
