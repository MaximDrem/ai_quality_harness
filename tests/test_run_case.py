"""Mock-тест AgentSession: auth, skills_dir, вложения, события и логи."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from client import AgentEvent, ToolCall
from example.agent.session import run_case
from example.models import ArtifactSpec, AttachmentSpec, RunResult, TestCase


class FakeSession:
    kwargs = None
    calls = []

    def __init__(self, **kwargs):
        type(self).kwargs = kwargs
        type(self).calls = []
        self.work_dir = Path(kwargs["work_dir"])
        self.instance = SimpleNamespace(proxy=None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def create_session(self, title=None):
        return "session"

    def actions(self, message, **kwargs):
        type(self).calls.append((message, kwargs))
        index = len(type(self).calls)
        path = self.work_dir / ("analysis.md" if index == 1 else "result.pptx")
        path.write_text("data", encoding="utf-8")
        event = AgentEvent(type="message.part.updated", session_id="session", raw={})
        event.tool_calls.append(
            ToolCall(tool="write", state="completed", input={"filePath": str(path)}, output="")
        )
        yield event


class RunCaseTests(unittest.TestCase):
    def test_auth_skills_attachments_and_intermediate_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pptx"
            source.write_bytes(b"source")
            case = TestCase(
                id="style",
                user_phrase="clone",
                skill="presentation_style",
                artifact_type=".pptx",
                case_dir=str(root),
                attachments=[AttachmentSpec(path="source.pptx", name="template")],
                artifacts=[
                    ArtifactSpec(name="analysis", artifact_type=".md", stage="intermediate"),
                    ArtifactSpec(name="result", artifact_type=".pptx", stage="final"),
                ],
            )
            evaluated = AsyncMock(return_value=RunResult(test_case=case))
            with patch("client.AgentSession", FakeSession), patch("example.agent.session.evaluate_case", evaluated):
                result = asyncio.run(
                    run_case(
                        case,
                        root / "runs",
                        asyncio.Semaphore(1),
                        model="provider/model",
                        auth_mode="prod",
                        skills_dir="/tmp/config",
                    )
                )
            self.assertIs(result.test_case, case)
            self.assertEqual(FakeSession.kwargs["auth_mode"], "prod")
            self.assertEqual(FakeSession.kwargs["skills_dir"], Path("/tmp/config"))
            self.assertEqual(len(FakeSession.calls), 2)
            self.assertEqual(FakeSession.calls[0][1]["attachments"], [str(source)])
            self.assertNotIn("attachments", FakeSession.calls[1][1])
            context = evaluated.await_args.kwargs["artifact_context"]
            self.assertEqual(len(context.select(["analysis"])), 1)
            self.assertEqual(context.paths(["template"]), [str(source)])
            self.assertTrue(context.paths(["final"])[0].endswith("result.pptx"))
            self.assertTrue((root / "runs" / "run_1" / "style" / "raw.log").is_file())
            self.assertIn("TOOL write", (root / "runs" / "run_1" / "style" / "agent.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
