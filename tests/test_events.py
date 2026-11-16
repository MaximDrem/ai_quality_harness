"""Тесты routing, контекста и завершённых multipart-сообщений."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from example.agent.events import collect_context_usage, collect_skills_tools, extract_final_message


def completed(message_id):
    return SimpleNamespace(
        type="message.updated",
        properties={"info": {"id": message_id, "role": "assistant", "finish": "stop", "time": {"completed": 1}}},
    )


def text_part(message_id, part_id, text):
    return SimpleNamespace(
        type="message.part.updated",
        properties={"part": {"id": part_id, "messageID": message_id, "type": "text", "text": text}},
    )


class EventTests(unittest.TestCase):
    def test_context_uses_latest_value(self):
        events = [
            SimpleNamespace(type="session.context.usage", properties={"size": 100, "threshold": 1000, "percent": 10}),
            SimpleNamespace(type="session.context.usage", properties={"size": 250, "threshold": 1000, "percent": 25}),
        ]
        self.assertEqual(
            collect_context_usage(events),
            {"tokens": 250, "limit": 1000, "percent": 25},
        )

    def test_routing_uses_completed_skill_calls_only(self):
        events = []
        for state in ("pending", "running", "completed"):
            events.append(
                SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            tool="skill",
                            state=state,
                            is_skill=True,
                            input={"name": "expected"},
                        )
                    ]
                )
            )
        skills, _ = collect_skills_tools(events)
        self.assertEqual(skills, ["expected"])

    def test_idle_does_not_create_final_message_without_completed_assistant(self):
        events = [
            text_part("unfinished", "part", "Не завершено"),
            SimpleNamespace(type="session.status", properties={"status": {"type": "idle"}}),
        ]
        self.assertEqual(extract_final_message(events), ("", {}))

    def test_latest_completed_message_without_text_does_not_reuse_older_answer(self):
        events = [text_part("old", "part", "Старый ответ"), completed("old"), completed("new")]
        self.assertEqual(extract_final_message(events), ("", {}))

    def test_multipart_final_message_preserves_latest_snapshots_in_order(self):
        events = [
            text_part("message", "first", "Первая часть"),
            text_part("message", "second", "Вторая часть"),
            text_part("message", "first", "Первая часть, обновлённая"),
            completed("message"),
        ]
        text, raw = extract_final_message(events)
        self.assertEqual(text, "Первая часть, обновлённая\nВторая часть")
        self.assertEqual(len(raw["parts"]), 2)


if __name__ == "__main__":
    unittest.main()
