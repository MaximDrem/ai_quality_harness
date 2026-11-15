"""Взаимодействие с агентом: клиент, сессия, поток действий, артефакты."""
from __future__ import annotations

from .artifacts import artifacts_from_event, dedupe
from .client import AgentClient, parse_model_ref
from .events import extract_error, parse_event
from .result import AgentResult, build_result
from .runner import AgentRunner, to_thread
from .session import AgentSession

__all__ = [
    "AgentClient",
    "AgentSession",
    "AgentRunner",
    "AgentResult",
    "build_result",
    "parse_event",
    "parse_model_ref",
    "extract_error",
    "artifacts_from_event",
    "dedupe",
    "to_thread",
]