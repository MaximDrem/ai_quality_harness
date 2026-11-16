"""Разбор событий агента: скилы/тулы, артефакты, токены, ошибки.

Работает с событиями нового `client`, не запуская клиент сам. Дополнительно
извлекает завершённый multipart final_message и фактический размер контекста.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

# Расширения, которые считаем артефактом.
ARTIFACT_EXTS = {".pptx", ".pdf", ".docx", ".xlsx", ".md", ".json", ".csv", ".png", ".jpg", ".svg", ".html"}

# Путь/имя файла артефакта в тексте вывода тула.
_ARTIFACT_FILE_RE = re.compile(r"[\w./~@-]+\.[A-Za-z0-9]+", re.IGNORECASE)


def collect_skills_tools(events: list) -> tuple[List[str], List[str]]:
    """Разобрать события и собрать уникальные скилы и тулы (в порядке появления)."""
    skills: List[str] = []
    tools: List[str] = []
    seen: Dict[str, bool] = {}

    def _push(name: str, target: list) -> None:
        if name and not seen.get(name):
            seen[name] = True
            target.append(name)

    for ev in events:
        for tc in getattr(ev, "tool_calls", None) or []:
            raw_tool = tc.tool or "unknown"
            is_skill = (
                tc.is_skill
                or raw_tool == "skill"
                or raw_tool.startswith("skill:")
            )
            if not is_skill:
                _push(raw_tool, tools)
                continue
            if tc.state != "completed":
                continue
            name = None
            if isinstance(tc.input, dict):
                name = tc.input.get("name")
            name = str(name or "").strip() if name is not None else ""
            if raw_tool.startswith("skill:") and not name:
                name = raw_tool.split(":", 1)[1]
            if name:
                _push(name, skills)
            elif tc.is_skill:
                _push(raw_tool, skills)
    return skills, tools


def paths_from_text(text: str) -> List[str]:
    if not isinstance(text, str) or not text.strip():
        return []
    return [item.strip() for item in _ARTIFACT_FILE_RE.findall(text)]


def paths_from_tool_input(tc) -> List[str]:
    if (tc.tool or "") not in ("write", "edit") or not isinstance(tc.input, dict):
        return []
    result = []
    for key in ("filePath", "path", "filename"):
        value = tc.input.get(key)
        if isinstance(value, str) and value.strip():
            result.append(value.strip())
    return result


def paths_from_tool_output(tc) -> List[str]:
    """Извлечь пути/имена файлов из output завершённого тула."""
    out = tc.output
    if not isinstance(out, str) or not out.strip():
        return []
    stem = (tc.tool or "").split(":", 1)[0]
    if stem in ("skill",):
        return []
    return [o.strip() for o in _ARTIFACT_FILE_RE.findall(out)]


def agent_produced_artifact(work_dir: Path, output_paths: List[str]) -> bool:
    """Есть ли уже на диске хотя бы один артефакт-файл от агента.

    Проверяется по `output_paths` (путям/именам из вывода тулов) и по сканне
    рабочего каталога. Используется для решения, слать ли агенту «Продолжить».
    """
    for p in output_paths:
        s = (p or "").strip()
        if not s:
            continue
        pp = Path(s)
        if not pp.is_absolute():
            pp = work_dir / pp
        if pp.exists() and pp.is_file() and pp.suffix.lower() in ARTIFACT_EXTS:
            return True
    if work_dir.is_dir():
        try:
            for f in work_dir.rglob("*"):
                if f.is_file() and f.stat().st_size > 0 and f.suffix.lower() in ARTIFACT_EXTS:
                    return True
        except OSError:
            pass
    return False


def collect_tokens(events) -> Dict[str, int]:
    """Посчитать суммарное потребление токенов агентом по событиям.

    Источник — шаги `step-finish` (part.type == "step-finish"), где сервер
    присылает фактический расход токенов на завершившийся шаг. Суммируем по
    всем шагам — это и есть расход на весь прогон кейса (включая «Продолжить»).
    """
    tokens = {"input": 0, "output": 0, "reasoning": 0, "total": 0}
    for ev in events:
        part = ev.properties.get("part") or {}
        if not isinstance(part, dict) or part.get("type") != "step-finish":
            continue
        t = part.get("tokens") or {}
        if not isinstance(t, dict):
            continue
        tokens["input"] += int(t.get("input") or 0)
        tokens["output"] += int(t.get("output") or 0)
        tokens["reasoning"] += int(t.get("reasoning") or 0)
        tokens["total"] += int(t.get("total") or 0)
    return tokens


def collect_context_usage(events) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for event in events:
        if getattr(event, "type", "") != "session.context.usage":
            continue
        properties = getattr(event, "properties", {}) or {}
        if isinstance(properties, dict) and properties.get("size") is not None:
            result = {
                "tokens": int(properties.get("size") or 0),
                "limit": int(properties.get("threshold") or 0),
                "percent": float(properties.get("percent") or 0),
            }
    return result


def extract_final_message(events) -> tuple[str, Dict[str, Any]]:
    completed = {}
    selected_id = None
    for event in events:
        if getattr(event, "type", "") != "message.updated":
            continue
        info = (getattr(event, "properties", {}) or {}).get("info") or {}
        if (
            info.get("role") == "assistant"
            and info.get("finish") == "stop"
            and info.get("time", {}).get("completed")
        ):
            selected_id = info.get("id")
            completed[selected_id] = info
    if selected_id is None:
        return "", {}
    parts = {}
    for index, event in enumerate(events):
        if getattr(event, "type", "") != "message.part.updated":
            continue
        part = (getattr(event, "properties", {}) or {}).get("part") or {}
        if (
            part.get("messageID") == selected_id
            and part.get("type") == "text"
            and part.get("text")
        ):
            part_id = part.get("id") or f"index:{index}"
            first_index = parts.get(part_id, (index, None, None))[0]
            parts[part_id] = (first_index, part["text"], part)
    if not parts:
        return "", {}
    ordered = sorted(parts.values(), key=lambda item: item[0])
    text = "\n".join(item[1] for item in ordered)
    return text, {
        "message": completed[selected_id],
        "parts": [item[2] for item in ordered],
    }


def extract_artifacts(res, work_dir: Path, output_paths: List[str], expected_type: str) -> List[str]:
    """Собрать реальные пути артефактов (файлов), созданных агентом."""
    found: List[str] = []
    exts = {expected_type, ".pptx", ".pdf", ".docx", ".xlsx", ".md", ".json", ".csv"}

    def _add(p: str) -> None:
        s = p.strip()
        if not s:
            return
        pp = Path(s)
        if not pp.is_absolute():
            pp = work_dir / pp
        if pp.exists() and pp.is_file() and pp not in found:
            found.append(str(pp))

    for a in getattr(res, "artifacts", None) or []:
        _add(getattr(a, "path", ""))
    for p in output_paths:
        _add(p)

    if work_dir.is_dir():
        for p in sorted(work_dir.rglob("*")):
            if p.is_file() and p.stat().st_size > 0 and p.suffix.lower() in exts:
                _add(str(p))
    return found


def extract_error(ev) -> str:
    """Текст ошибки из события сессии."""
    err = ev.properties.get("error")
    if isinstance(err, dict):
        return err.get("message") or json.dumps(err, ensure_ascii=False)
    if isinstance(err, str):
        return err
    return str(err)