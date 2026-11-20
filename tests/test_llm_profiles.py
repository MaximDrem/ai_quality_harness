"""Offline-тесты выбора judge-профиля по сценарию."""

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

from example.checks.llm import clear_llm_profile_cache, get_llm_profile
from example.judge.client import judge_text
from example.judge.runner import run_judge
from example.models import Artifact, ArtifactContext, Check, TestCase


class LlmProfileTests(unittest.TestCase):
    def tearDown(self):
        clear_llm_profile_cache()

    def test_presentations_alias_uses_presentation_profile(self):
        self.assertIs(get_llm_profile("presentations"), get_llm_profile("presentation"))
        self.assertIn("презентаций", get_llm_profile("presentations").SYSTEM_PROMPT)

    def test_custom_profile_is_loaded_without_runner_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "sample_profile"
            package.mkdir()
            (package / "__init__.py").write_text(
                "from pydantic import BaseModel\n"
                "class JudgeResult(BaseModel):\n"
                "    success: str\n"
                "    reason: str\n"
                "SYSTEM_PROMPT = 'CUSTOM SYSTEM'\n"
                "def build_text_prompt(agent_text, user_instruction, check_instruction):\n"
                "    return 'CUSTOM TEXT: ' + agent_text\n"
                "def build_vision_prompt(user_instruction, check_instruction, number_of_images):\n"
                "    return 'CUSTOM VISION'\n",
                encoding="utf-8",
            )
            with patch("example.checks.llm.__path__", [directory]):
                clear_llm_profile_cache()
                profile = get_llm_profile("sample_profile")
                self.assertEqual(profile.SYSTEM_PROMPT, "CUSTOM SYSTEM")
                verdict = SimpleNamespace(success=True, reason="ok")
                with patch("example.judge.client._ainvoke_judge", AsyncMock(return_value=verdict)) as invoke:
                    result = asyncio.run(
                        judge_text("agent output", None, "check", scenario="sample_profile")
                    )
                self.assertTrue(result.success)
                messages = invoke.await_args.args[0]
                self.assertEqual(messages[0], ("system", "CUSTOM SYSTEM"))
                self.assertEqual(messages[1], ("human", "CUSTOM TEXT: agent output"))

    def test_runner_forwards_case_scenario(self):
        artifact = Artifact(path=None, role="final_message", kind="message", name="final_message", text="answer")
        check = Check(text="check", kind="llm", input="text", artifacts=["final_message"])
        case = TestCase(
            id="sample",
            user_phrase="test",
            skill="demo",
            artifact_type="",
            scenario="sample_profile",
            check_list=[check],
        )
        verdict = SimpleNamespace(success=True, reason="ok")
        with patch("example.judge.runner.judge_text", AsyncMock(return_value=verdict)) as judge:
            asyncio.run(run_judge(case, ArtifactContext(items=[artifact], processed=True)))
        self.assertEqual(judge.await_args.kwargs["scenario"], "sample_profile")

    def test_missing_profile_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, "не найден"):
            get_llm_profile("missing_profile")


if __name__ == "__main__":
    unittest.main()
