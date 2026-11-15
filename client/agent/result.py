"""Результат выполнения одного сообщения агенту и его сборка из событий."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.models import AgentEvent, Artifact, Reasoning, TextOutput, ToolCall
from .artifacts import artifacts_from_event, dedupe
from .events import extract_error, message_error


@dataclass
class AgentResult:
    """Результат выполнения одного сообщения агенту."""

    session_id: str
    events: List[AgentEvent] = field(default_factory=list)
    text_outputs: List[TextOutput] = field(default_factory=list)
    reasonings: List[Reasoning] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)
    error: Optional[str] = None
    # Структурированная ошибка session.error (дискриминант по name, детали в
    # data: APIError{message,statusCode,isRetryable,...}.
    error_data: Optional[Dict[str, Any]] = None
    # Попытки ретрая провайдера: [{attempt, message, next}] из
    # session.status {type: "retry"} (генерация при этом продолжается).
    retries: List[Dict[str, Any]] = field(default_factory=list)
    # Клиентский messageID (ULID, как Identifier.ascending("message")).
    message_id: Optional[str] = None

    @property
    def final_text(self) -> str:
        """Склеенный текст последнего ответа ассистента."""
        return "\n".join(t.text for t in self.text_outputs if t.text)


def build_result(
    session_id: str,
    events: List[AgentEvent],
    message_id: Optional[str] = None,
) -> AgentResult:
    """Собрать `AgentResult` из потока событий сессии.

    События других сессий игнорируются (глобальный канал общий для инстанса).
    """
    result = AgentResult(session_id=session_id, message_id=message_id, events=list(events))
    for ev in events:
        if ev.session_id and ev.session_id != session_id:
            continue
        result.text_outputs += ev.text_outputs
        result.reasonings += ev.reasonings
        result.tool_calls += ev.tool_calls
        result.artifacts += artifacts_from_event(ev)
        # Retry-попытки провайдера ({attempt, message, next}) — генерация
        # после них продолжается; попадают в result.retries для отчётности.
        if ev.is_retry:
            st = ev.status or {}
            result.retries.append(
                {
                    "attempt": st.get("attempt"),
                    "message": st.get("message"),
                    "next": st.get("next"),
                }
            )
        info_err = message_error(ev)
        if info_err is not None:
            result.error_data = result.error_data or info_err
        if ev.type == "session.error" and ev.session_id == session_id:
            err = ev.error
            if isinstance(err, dict) and err.get("name") != "MessageAbortedError":
                result.error_data = err
            elif isinstance(err, dict):
                result.error_data = result.error_data or err
            result.error = result.error or extract_error(ev)

    # Один и тот же путь приходит и из сообщений частичного ответа, и из
    # финального: отдаём каждый артефакт один раз.
    result.artifacts = dedupe(result.artifacts)

    if result.error_data and not result.error:
        data = result.error_data.get("data") if isinstance(result.error_data, dict) else None
        if isinstance(data, dict) and data.get("message"):
            result.error = str(data["message"])
        else:
            result.error = json.dumps(result.error_data, ensure_ascii=False)
    return result


__all__ = ["AgentResult", "build_result"]