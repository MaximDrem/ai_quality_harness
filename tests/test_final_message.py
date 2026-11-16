"""Тесты functional и text-проверок виртуального final_message без сети."""

from __future__ import annotations

import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

class DummyChatOpenAI:
    def __init__(self, *args, **kwargs):
        pass

    def with_structured_output(self, *args, **kwargs):
        return self


sys.modules.setdefault("langchain_openai", SimpleNamespace(ChatOpenAI=DummyChatOpenAI))
sys.modules.setdefault("langchain_core.messages", SimpleNamespace(HumanMessage=object))

from example.checks.functional import run_functional_check
from example.judge.runner import run_judge
from example.models import Artifact, ArtifactContext, Check, TestCase


class FinalMessageTests(unittest.TestCase):
    def test_functional_check_on_final_message(self):
        artifact = Artifact(
            path=None,
            role="final_message",
            name="final_message",
            kind="message",
            text="word word",
        )
        check = Check(text="repeated", kind="function", handler="text/repeated_words", artifacts=["final_message"])
        result = run_functional_check(check, ArtifactContext(items=[artifact]))
        self.assertFalse(result.success)
        self.assertEqual(result.artifact_path, None)
        self.assertEqual(len(artifact.results), 1)

    def test_text_judge_on_final_message_without_network(self):
        artifact = Artifact(
            path=None,
            role="final_message",
            name="final_message",
            kind="message",
            text="Полный ответ пользователю",
        )
        check = Check(text="Ответ полный", kind="llm", input="text", artifacts=["final_message"], metric="Answer")
        case = TestCase(id="message", user_phrase="test", skill="demo", artifact_type="", check_list=[check])
        verdict = SimpleNamespace(success=True, reason="Полный ответ")
        with patch("example.judge.runner.judge_text", AsyncMock(return_value=verdict)) as judge:
            results = asyncio.run(run_judge(case, ArtifactContext(items=[artifact])))
        self.assertTrue(results[0].success)
        self.assertEqual(len(artifact.results), 1)
        self.assertIn("Полный ответ пользователю", judge.await_args.kwargs["agent_text"])


if __name__ == "__main__":
    unittest.main()
