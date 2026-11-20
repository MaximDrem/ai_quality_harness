"""Сбор файловых артефактов агента и финального сообщения.

Сопоставляет YAML-описания с файлами из событий и work_dir, исключает uploads
и исходные вложения из финалов и создаёт виртуальный артефакт final_message.
В каждый артефакт добавляет связанные завершённые вызовы инструментов.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterable, List, Optional

from .agent.events import (
    extract_final_message,
    paths_from_text,
    paths_from_tool_input,
    paths_from_tool_output,
)
from .cases import artifact_specs, resolve_attachment_paths
from .models import Artifact, ArtifactContext, ArtifactSpec, TestCase


def _resolve_candidate(path: str, work_dir: Path) -> Optional[Path]:
    value = str(path or "").strip().strip("`\"'")
    if not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = work_dir / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def _matches(spec: ArtifactSpec, path: Path, work_dir: Path) -> bool:
    if spec.kind == "message":
        return False
    if spec.artifact_type and path.suffix.lower() != spec.artifact_type:
        return False
    if not spec.path:
        return True
    if Path(spec.path).is_absolute():
        return fnmatch.fnmatch(str(path), spec.path)
    try:
        relative = path.relative_to(work_dir).as_posix()
    except ValueError:
        return False
    pattern = spec.path.replace("\\", "/")
    if "/" not in pattern:
        return fnmatch.fnmatch(path.name, pattern)
    return fnmatch.fnmatch(relative, pattern)


class ArtifactCollector:
    def __init__(self, case: TestCase, work_dir: Path):
        self.case = case
        self.work_dir = work_dir.resolve()
        names = [spec.name for spec in artifact_specs(case)]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Имена артефактов кейса {case.id} не уникальны: {duplicates}")

    def declared_extensions(self) -> set[str]:
        return {
            spec.artifact_type.lower()
            for spec in artifact_specs(self.case)
            if spec.kind == "file" and spec.artifact_type
        }

    def _source_items(self) -> List[Artifact]:
        paths = resolve_attachment_paths(self.case)
        return [
            Artifact(
                path=path,
                role="source",
                name=attachment.name,
                artifact_type=Path(path).suffix.lower(),
                origin_path=path,
                processor=self.case.processor,
            )
            for attachment, path in zip(self.case.attachments, paths)
        ]

    def _final_message(self, result) -> Optional[Artifact]:
        text, raw = extract_final_message(getattr(result, "events", []) or [])
        if not text:
            return None
        return Artifact(
            path=None,
            role="final_message",
            name="final_message",
            kind="message",
            text=text,
            raw_structure=raw,
        )

    def _is_excluded_final(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.work_dir)
            if relative.parts and relative.parts[0] == "uploads":
                return True
        except ValueError:
            pass
        for attachment in self.case.attachments:
            source = Path(attachment.path)
            if not source.is_absolute():
                source = Path(self.case.case_dir) / source
            if path == source.resolve():
                return True
        return False

    def _candidate_paths(self, result, output_paths: Iterable[str]) -> List[Path]:
        candidates = list(output_paths)
        candidates.extend(getattr(item, "path", "") for item in (getattr(result, "artifacts", None) or []))
        paths: List[Path] = []
        seen = set()
        for candidate in candidates:
            path = _resolve_candidate(candidate, self.work_dir)
            if (
                path is not None
                and path.is_file()
                and path not in seen
                and not self._is_excluded_final(path)
            ):
                paths.append(path)
                seen.add(path)
        if self.work_dir.is_dir():
            for path in sorted(self.work_dir.rglob("*")):
                if not path.is_file() or path.stat().st_size <= 0:
                    continue
                resolved = path.resolve()
                if resolved not in seen and not self._is_excluded_final(resolved):
                    paths.append(resolved)
                    seen.add(resolved)
        return paths

    def _explicit_final_paths(self, result) -> List[Path]:
        if not [spec for spec in artifact_specs(self.case, True) if spec.kind != "message"]:
            return []
        message = self._final_message(result)
        if message is None:
            return []
        paths: List[Path] = []
        for candidate in paths_from_text(message.text, self.declared_extensions()):
            path = _resolve_candidate(candidate, self.work_dir)
            if path and path.is_file() and not self._is_excluded_final(path):
                paths.append(path)
        return paths

    def _paths_for_spec(self, spec: ArtifactSpec, explicit: List[Path], fallback: List[Path]) -> List[Path]:
        explicit_matches = [path for path in explicit if _matches(spec, path, self.work_dir)]
        candidates = explicit_matches or fallback
        return [path for path in candidates if _matches(spec, path, self.work_dir)]

    def _tool_calls_for_path(self, result, target: Optional[Path]) -> List[dict]:
        records: List[dict] = []
        seen = set()
        for event_index, event in enumerate(getattr(result, "events", []) or [], start=1):
            part = (getattr(event, "properties", {}) or {}).get("part") or {}
            for tool_call in getattr(event, "tool_calls", []) or []:
                state = getattr(tool_call, "state", "")
                if state not in ("completed", "error"):
                    continue
                call_key = (part.get("id") or f"event:{event_index}", getattr(tool_call, "tool", "unknown"), state)
                if call_key in seen:
                    continue
                seen.add(call_key)
                related = target is None
                if target is not None:
                    candidates = (
                        paths_from_tool_input(tool_call)
                        + paths_from_tool_output(tool_call, self.declared_extensions())
                    )
                    related = any(
                        _resolve_candidate(candidate, self.work_dir) == target
                        for candidate in candidates
                    )
                if related:
                    records.append({
                        "event_index": event_index,
                        "tool": getattr(tool_call, "tool", "unknown"),
                        "state": state,
                        "input": getattr(tool_call, "input", {}) or {},
                        "output": getattr(tool_call, "output", "") or "",
                        "error": getattr(tool_call, "error", "") or "",
                        "is_skill": bool(getattr(tool_call, "is_skill", False)),
                    })
        return records

    def _attach_tool_calls(self, result, items: List[Artifact]) -> None:
        for artifact in items:
            if artifact.role == "source":
                continue
            if artifact.kind == "message":
                artifact.tool_calls = self._tool_calls_for_path(result, None)
            elif artifact.path:
                target = Path(artifact.origin_path or artifact.path).resolve()
                artifact.tool_calls = self._tool_calls_for_path(result, target)

    def has_final(self, result, output_paths: Iterable[str]) -> bool:
        specs = artifact_specs(self.case, True)
        if not specs:
            return False
        message = self._final_message(result)
        file_specs = [spec for spec in specs if spec.kind != "message"]
        required = [spec for spec in specs if spec.required]
        selected = required or specs

        def present(spec: ArtifactSpec) -> bool:
            if spec.kind == "message":
                return bool(message and message.text.strip())
            return bool(self._paths_for_spec(spec, explicit, fallback))

        if not file_specs:
            if required:
                return all(present(spec) for spec in selected)
            return any(present(spec) for spec in selected)
        explicit = self._explicit_final_paths(result)
        fallback = self._candidate_paths(result, output_paths)
        if required:
            return all(present(spec) for spec in selected)
        return any(present(spec) for spec in selected)

    def finalize(self, result, output_paths: Iterable[str]) -> ArtifactContext:
        items: List[Artifact] = []
        owners = {}
        final_specs = [spec for spec in artifact_specs(self.case, True) if spec.kind != "message"]
        supporting_specs = [spec for spec in artifact_specs(self.case, False) if spec.kind != "message"]
        explicit = self._explicit_final_paths(result) if final_specs else []
        fallback = self._candidate_paths(result, output_paths) if final_specs or supporting_specs else []

        for specs, role in ((supporting_specs, "supporting"), (final_specs, "final")):
            for spec in specs:
                spec_explicit = explicit if role == "final" else []
                for path in self._paths_for_spec(spec, spec_explicit, fallback):
                    key = str(path)
                    if owners.get(key, spec.name) != spec.name:
                        raise ValueError(
                            f"Файл {path} одновременно соответствует артефактам "
                            f"{[owners[key], spec.name]}"
                        )
                    if key in owners:
                        continue
                    owners[key] = spec.name
                    items.append(
                        Artifact(
                            path=key,
                            role=role,
                            name=spec.name,
                            artifact_type=spec.artifact_type or path.suffix.lower(),
                            origin_path=key,
                            processor=spec.processor,
                        )
                    )
        items = self._source_items() + items
        message = self._final_message(result)
        if message is not None:
            items.append(message)
        self._attach_tool_calls(result, items)
        return ArtifactContext(items=items)


def validation_context(case: TestCase, artifact_path: str) -> ArtifactContext:
    path = Path(artifact_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Файл для валидации не найден: {path}")
    specs = [spec for spec in artifact_specs(case, True) if spec.kind != "message"]
    matches = [
        spec
        for spec in specs
        if any(_matches(spec, path, parent) for parent in path.parents)
    ]
    if not matches:
        raise ValueError(f"Файл {path} не соответствует финальным артефактам кейса {case.id}")
    if len(matches) > 1:
        names = [spec.name for spec in matches]
        raise ValueError(f"Файл {path} одновременно соответствует артефактам {names}")
    spec = matches[0]
    final = Artifact(
        path=str(path),
        role="final",
        name=spec.name,
        artifact_type=spec.artifact_type or path.suffix.lower(),
        origin_path=str(path),
        processor=spec.processor,
    )
    collector = ArtifactCollector(case, path.parent)
    return ArtifactContext(items=collector._source_items() + [final])
