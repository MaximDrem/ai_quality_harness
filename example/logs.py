"""Запись логов прогона кейса.

`record_event` сохраняет полный технический raw.log, HumanLogWriter создаёт
компактный человекочитаемый agent.log, а write_case_log записывает чек-лист,
роутинг, артефакты, вопросы, время и токены.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List


def serialize(value: Any) -> str:
    """Безопасно сериализовать произвольное значение в строку."""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def extract_questions(ev) -> List[Dict[str, Any]]:
    """Из события `question.asked` вытащить список вопросов агента."""
    if getattr(ev, "type", None) != "question.asked":
        return []
    req_id = (ev.properties or {}).get("id")
    questions = (ev.properties or {}).get("questions") or []
    out: List[Dict[str, Any]] = []
    for i, q in enumerate(questions):
        out.append(
            {
                "index": i,
                "id": req_id,
                "question": q.get("question") if isinstance(q, dict) else str(q),
                "header": q.get("header") if isinstance(q, dict) else None,
                "options": q.get("options") if isinstance(q, dict) else None,
                "multiple": q.get("multiple") if isinstance(q, dict) else None,
                "custom": q.get("custom") if isinstance(q, dict) else None,
            }
        )
    return out


def _event_diag(ev) -> str:
    """Компактная диагностика события: ошибки тулов, статусы, модель.

    Исходный `raw` остаётся в отдельном поле, здесь — только суть.
    """
    infer = getattr(ev, "info", None)
    buf: List[str] = []
    # Ошибки тулов: текст + тело.
    for tc in getattr(ev, "tool_calls", []):
        tool = getattr(tc, "tool", None)
        state = getattr(tc, "state", None)
        err = getattr(tc, "error", None)
        if not state:
            continue
        if state in ("error",) or err:
            out = f"tool:{tool} state={state}"
            if err:
                out += f" error={err}"
            # Раскрыть тело ошибки из raw (404/502, No such model).
            raw = ev.raw
            if isinstance(raw, dict):
                body = None
                for key in ("error", "data", "message"):
                    if isinstance(raw.get(key), (dict, str)):
                        body = raw.get(key)
                        break
                if isinstance(body, dict) and "detail" in body:
                    out += f" detail={body.get('detail')}"
            buf.append(out)
    # session.status: retry/error.
    st = (ev.properties or {}).get("status") if getattr(ev, "properties", None) else None
    if isinstance(st, dict):
        stype = st.get("type")
        if stype in ("retry", "error"):
            msg = st.get("message")
            extra = ""
            if isinstance(msg, str):
                try:
                    parsed = json.loads(msg)
                    extra = f" {parsed.get('detail') or parsed.get('message')}"
                except (ValueError, TypeError):
                    extra = f" {msg}"
            buf.append(f"status:{stype} attempt={st.get('attempt')}{extra}")
    # message.updated: finish/model.
    if getattr(ev, "type", None) == "message.updated" and isinstance(infer, dict):
        fin = infer.get("finish")
        m = infer.get("model")
        if m:
            buf.append(f"model={m.get('modelID')}:{m.get('providerID')} finish={fin or ''}")
    return " | ".join(x for x in buf if x)


def _event_record(ev) -> Dict[str, Any]:
    """Собрать JSON-запись одного агент-события (kind=agent_event)."""
    return {
        "kind": "agent_event",
        "ts": time.time(),
        "type": ev.type,
        "session_id": ev.session_id,
        "properties": ev.properties,
        "text": [t.text for t in getattr(ev, "text_outputs", []) if t.text],
        "reasoning": [r.text for r in getattr(ev, "reasonings", []) if r.text],
        "tool_calls": [
            {
                "tool": tc.tool,
                "state": tc.state,
                "input": tc.input,
                "output": tc.output,
                "error": tc.error,
                "toolkit": tc.toolkit,
                "is_skill": tc.is_skill,
            }
            for tc in getattr(ev, "tool_calls", [])
        ],
        "questions": extract_questions(ev),
        # Читаемая диагностика (ошибки тулов, retry/error, модель).
        "diag": _event_diag(ev),
        "raw": ev.raw,
    }


def _api_trace_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Обернуть сетевую запись прокси в kind=api_trace."""
    out = {"kind": "api_trace"}
    out.update(rec)
    return out


def record_event(ev, raw_writer) -> None:
    """Записать событие агента строкой JSON в raw-лог."""
    if hasattr(raw_writer, "write_event"):
        raw_writer.write_event(ev)
        return
    raw_writer.write(serialize(_event_record(ev)) + "\n")
    raw_writer.flush()


class HumanLogWriter:
    def __init__(self, path: Path):
        self._stream = path.open("w", encoding="utf-8")
        self._last_context = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stream.close()

    def _write(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._stream.write(f"[{stamp}] {message}\n")
        self._stream.flush()

    def write_event(self, ev) -> None:
        for tool_call in getattr(ev, "tool_calls", []) or []:
            state = getattr(tool_call, "state", "")
            if state not in ("completed", "error"):
                continue
            tool = getattr(tool_call, "tool", "unknown")
            is_skill = getattr(tool_call, "is_skill", False) or tool == "skill" or tool.startswith("skill:")
            data = getattr(tool_call, "input", {}) or {}
            if is_skill and state == "completed":
                self._write(f"SKILL {data.get('name') or tool}")
                continue
            if not is_skill:
                target = data.get("filePath") or data.get("path") or data.get("command") or ""
                self._write(f"TOOL {tool} {target} — {state}")
        if getattr(ev, "type", "") == "session.context.usage":
            properties = getattr(ev, "properties", {}) or {}
            current = (properties.get("size"), properties.get("threshold"), properties.get("percent"))
            if current != self._last_context and current[0] is not None:
                self._last_context = current
                self._write(f"CONTEXT {current[0]}/{current[1]} tokens ({current[2]}%)")
        if getattr(ev, "type", "") == "message.part.updated":
            for text in getattr(ev, "text_outputs", []) or []:
                if text.text:
                    self._write(f"ASSISTANT {text.text}")
        if getattr(ev, "type", "") == "question.asked":
            for question in extract_questions(ev):
                self._write(f"QUESTION {question.get('question')}")
        if getattr(ev, "type", "") == "session.error":
            error = (getattr(ev, "properties", {}) or {}).get("error")
            self._write(f"ERROR {error}")
        if getattr(ev, "type", "") == "session.status":
            status = (getattr(ev, "properties", {}) or {}).get("status", {}).get("type")
            if status == "idle":
                self._write("IDLE")


class RawLogWriter:
    """Потокобезопасный писатель raw.log.

    События агента (kind=agent_event) и сетевые обращения прокси
    (kind=api_trace) пишутся в один файл по мере поступления. Доступ под
    локом.
    """

    def __init__(self, path: Path, tag: str = ""):
        self._path = Path(path)
        self._tag = tag or self._path.parent.name
        self._lock = threading.Lock()
        self._f = open(path, "w", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self._path

    def write_event(self, ev) -> None:
        """Онлайн-запись агент-события."""
        self._emit(_event_record(ev))

    def write_api_trace(self, rec: Dict[str, Any]) -> None:
        """Онлайн-запись сетевого обращения прокси к API LLM.

        `rec` — словарь трейса (method, path, model, status, ...). Метка
        инстанса (порт прокси) добавляется автоматически.
        """
        rec = dict(rec)
        rec.setdefault("instance", self._tag)
        self._emit(_api_trace_record(rec))

    def _emit(self, rec: Dict[str, Any]) -> None:
        line = serialize(rec) + "\n"
        with self._lock:
            self._f.write(line)
            self._f.flush()

    def close(self) -> None:
        with self._lock:
            try:
                self._f.flush()
            finally:
                self._f.close()

    def __enter__(self) -> "RawLogWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def write_case_log(
    runs_dir,
    case,
    skills,
    tools,
    artifacts,
    render_photos,
    questions,
    agent_time_sec: float = 0.0,
    tokens=None,
    continuations: int = 0,
    artifact_context=None,
) -> None:
    """Сводный лог кейса: check_list, скилы/тулы/артефакты, фото, вопросы,
    метрики и счётчик «Продолжить». `artifacts` — абсолютные пути.
    """
    tokens = tokens or {}
    with open(runs_dir / "skills_tools_artifact.log", "w", encoding="utf-8") as f:
        f.write("# Check list\n")
        for c in case.check_list:
            f.write(f"[{c.kind}] {c.text}\n")
        if not case.check_list:
            f.write("(none)\n")

        f.write("\n# Skills called\n")
        for s in skills:
            f.write(f"{s}\n")
        if not skills:
            f.write("(none)\n")

        f.write("\n# Tools called\n")
        for t in tools:
            f.write(f"{t}\n")
        if not tools:
            f.write("(none)\n")

        f.write("\n# Artifacts\n")
        for a in artifacts:
            f.write(f"{a}\n")
        if not artifacts:
            f.write("(none)\n")

        if artifact_context is not None:
            f.write("\n# Artifact versions and roles\n")
            for item in artifact_context.items:
                f.write(
                    f"role={item.role} name={item.name} path={item.path} "
                    f"origin={item.origin_path} event={item.event_index}\n"
                )

        f.write("\n# Render photos (for LLM check)\n")
        for ph in render_photos:
            f.write(f"{ph}\n")
        if not render_photos:
            f.write("(none)\n")

        f.write("\n# Questions asked by agent (answers will be produced by LLM)\n")
        for q in questions:
            f.write(serialize(q) + "\n")
        if not questions:
            f.write("(none)\n")

        # Метрики прогона: время агента и токены.
        f.write("\n# Metrics\n")
        f.write(f"agent_time_sec: {agent_time_sec:.3f}\n")
        f.write(
            "tokens: input={input} output={output} reasoning={reasoning} total={total}\n".format(
                **{k: tokens.get(k, 0) for k in ("input", "output", "reasoning", "total")}
            )
        )
        f.write(f"continuations (Продолжить sent): {continuations}\n")