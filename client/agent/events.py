"""Разбор SSE-событий сервера (BusEvent) в нормализованный `AgentEvent`.

Сервер шлёт `message.part.updated` с частями ответа: `text`, `reasoning`,
`tool` (скилл/инструмент). Здесь они превращаются в `TextOutput`/`Reasoning`/
`ToolCall`, чтобы верхние слои работали с типизированными значениями.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..core.models import AgentEvent, Reasoning, TextOutput, ToolCall


def parse_event(obj: Dict[str, Any]) -> AgentEvent:
    etype = obj.get("type") or obj.get("_event") or "unknown"
    props = obj.get("properties") or {}
    sid = props.get("sessionID") or props.get("session_id")
    ev = AgentEvent(type=etype, session_id=sid, properties=props, raw=obj)

    if etype == "message.part.updated":
        part = props.get("part") or {}
        ptype = part.get("type")
        if ptype == "text":
            text = part.get("text") or ""
            if text:
                ev.text_outputs.append(TextOutput(text=text))
        elif ptype == "reasoning":
            text = part.get("text") or ""
            if text:
                ev.reasonings.append(Reasoning(text=text))
        elif ptype == "tool":
            ev.tool_calls.append(_parse_tool_call(part))
    return ev


def _parse_tool_call(part: Dict[str, Any]) -> ToolCall:
    state = part.get("state") or {}
    status = (
        state.get("status")
        if isinstance(state, dict)
        else part.get("status")
    )
    if not isinstance(status, str):
        status = state.get("type") if isinstance(state, dict) else None
    status = status or "running"
    info = state.get("info") if isinstance(state, dict) else None
    toolkit = (info or {}).get("toolkit") if isinstance(info, dict) else None
    if not toolkit:
        toolkit = part.get("toolkit")
    output = (
        state.get("output")
        if isinstance(state, dict)
        else (part.get("output"))
    )
    if isinstance(output, dict):
        output = json.dumps(output, ensure_ascii=False)
    err = state.get("error") if isinstance(state, dict) else part.get("error")
    return ToolCall(
        tool=part.get("tool") or "unknown",
        state=status,
        input=state.get("input") if isinstance(state, dict) else (part.get("input") or {}),
        output=output if isinstance(output, str) else (str(output) if output is not None else None),
        error=err if isinstance(err, str) else (str(err) if err is not None else None),
        toolkit=toolkit,
    )


def extract_error(ev: AgentEvent) -> str:
    """Человекочитаемое сообщение ошибки из `properties.error`."""
    err = ev.properties.get("error")
    if isinstance(err, dict):
        return err.get("message") or json.dumps(err, ensure_ascii=False)
    if isinstance(err, str):
        return err
    return str(err)


def message_error(ev: AgentEvent) -> Optional[Dict[str, Any]]:
    """Ошибка шага на assistant-сообщении (как UI: info.error в message.updated).

    `MessageAbortedError` ошибкой не считается — это штатный abort сессии.
    """
    if ev.type != "message.updated":
        return None
    info = ev.properties.get("info") or {}
    info_err = info.get("error") if isinstance(info, dict) else None
    if isinstance(info_err, dict) and info_err.get("name") != "MessageAbortedError":
        return info_err
    return None


__all__ = ["parse_event", "extract_error", "message_error"]