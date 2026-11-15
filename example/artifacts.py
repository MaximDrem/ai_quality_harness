from __future__ import annotations

import fnmatch
import hashlib
import re
import shutil
from pathlib import Path
from typing import Iterable, List, Optional

from .agent.events import paths_from_text, paths_from_tool_input, paths_from_tool_output
from .cases import artifact_specs, resolve_attachment_paths
from .models import ArtifactContext, ArtifactSpec, CollectedArtifact, TestCase


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


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "artifact"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArtifactCollector:
    def __init__(self, case: TestCase, work_dir: Path, snapshot_dir: Path):
        self.case = case
        self.work_dir = work_dir.resolve()
        self.snapshot_dir = snapshot_dir
        self.intermediate: List[CollectedArtifact] = []
        self._seen_snapshots = set()
        names = [spec.name for spec in artifact_specs(case)]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Имена артефактов кейса {case.id} не уникальны: {duplicates}")

    def _event_paths(self, event) -> List[Path]:
        candidates: List[str] = []
        for tool_call in getattr(event, "tool_calls", None) or []:
            if getattr(tool_call, "state", "") != "completed":
                continue
            candidates.extend(paths_from_tool_input(tool_call))
            candidates.extend(paths_from_tool_output(tool_call))
        for artifact in getattr(event, "artifacts", None) or []:
            candidates.append(getattr(artifact, "path", ""))
        for text in getattr(event, "text_outputs", None) or []:
            candidates.extend(paths_from_text(getattr(text, "text", "")))
        paths: List[Path] = []
        seen = set()
        for candidate in candidates:
            path = _resolve_candidate(candidate, self.work_dir)
            if path is not None and path not in seen:
                paths.append(path)
                seen.add(path)
        return paths

    def capture_event(self, event, event_index: int) -> None:
        specs = artifact_specs(self.case, "intermediate")
        if not specs:
            return
        paths = self._event_paths(event)
        known = set(paths)
        if self.work_dir.is_dir():
            for path in sorted(self.work_dir.rglob("*")):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if resolved not in known and any(_matches(spec, resolved, self.work_dir) for spec in specs):
                    paths.append(resolved)
                    known.add(resolved)
        for path in paths:
            if not path.is_file():
                continue
            for spec in specs:
                if not _matches(spec, path, self.work_dir):
                    continue
                key = (spec.name, str(path), _file_digest(path))
                if key in self._seen_snapshots:
                    continue
                self._seen_snapshots.add(key)
                self.snapshot_dir.mkdir(parents=True, exist_ok=True)
                snapshot_index = len(self.intermediate) + 1
                filename = f"{snapshot_index:05d}_{event_index:05d}_{_safe_name(spec.name)}_{path.name}"
                snapshot = self.snapshot_dir / filename
                shutil.copy2(path, snapshot)
                self.intermediate.append(
                    CollectedArtifact(
                        path=str(snapshot),
                        role="intermediate",
                        name=spec.name,
                        artifact_type=spec.artifact_type or path.suffix.lower(),
                        origin_path=str(path),
                        event_index=event_index,
                        processor=spec.processor,
                    )
                )

    def _source_items(self) -> List[CollectedArtifact]:
        paths = resolve_attachment_paths(self.case)
        items: List[CollectedArtifact] = []
        for attachment, path in zip(self.case.attachments, paths):
            items.append(
                CollectedArtifact(
                    path=path,
                    role="source",
                    name=attachment.name,
                    artifact_type=Path(path).suffix.lower(),
                    origin_path=path,
                    processor=self.case.processor,
                )
            )
        return items

    def _candidate_paths(self, result, output_paths: Iterable[str]) -> List[Path]:
        candidates = list(output_paths)
        candidates.extend(getattr(item, "path", "") for item in (getattr(result, "artifacts", None) or []))
        paths: List[Path] = []
        seen = set()
        for candidate in candidates:
            path = _resolve_candidate(candidate, self.work_dir)
            if path is not None and path.is_file() and path not in seen:
                paths.append(path)
                seen.add(path)
        if self.work_dir.is_dir():
            for path in sorted(self.work_dir.rglob("*")):
                if not path.is_file() or path.stat().st_size <= 0:
                    continue
                resolved = path.resolve()
                if resolved not in seen:
                    paths.append(resolved)
                    seen.add(resolved)
        return paths

    def has_final(self, result, output_paths: Iterable[str]) -> bool:
        specs = artifact_specs(self.case, "final")
        if not specs:
            return False
        paths = self._candidate_paths(result, output_paths)
        required = [spec for spec in specs if spec.required]
        if not required:
            return any(
                _matches(spec, path, self.work_dir)
                for spec in specs
                for path in paths
            )
        return all(
            any(_matches(spec, path, self.work_dir) for path in paths)
            for spec in required
        )

    def finalize(self, result, output_paths: Iterable[str]) -> ArtifactContext:
        final: List[CollectedArtifact] = []
        seen = set()
        specs = artifact_specs(self.case, "final")
        for path in self._candidate_paths(result, output_paths):
            matches = [item for item in specs if _matches(item, path, self.work_dir)]
            if len(matches) > 1:
                names = [item.name for item in matches]
                raise ValueError(f"Файл {path} одновременно соответствует артефактам {names}")
            if not matches or str(path) in seen:
                continue
            spec = matches[0]
            seen.add(str(path))
            final.append(
                CollectedArtifact(
                    path=str(path),
                    role="final",
                    name=spec.name,
                    artifact_type=spec.artifact_type or path.suffix.lower(),
                    origin_path=str(path),
                    processor=spec.processor,
                )
            )
        return ArtifactContext(items=self._source_items() + self.intermediate + final)


def validation_context(case: TestCase, artifact_path: str) -> ArtifactContext:
    path = Path(artifact_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Файл для валидации не найден: {path}")
    specs = artifact_specs(case, "final")
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
    final = CollectedArtifact(
        path=str(path),
        role="final",
        name=spec.name,
        artifact_type=spec.artifact_type or path.suffix.lower(),
        origin_path=str(path),
        processor=spec.processor,
    )
    collector = ArtifactCollector(case, path.parent, path.parent / "intermediate_artifacts")
    return ArtifactContext(items=collector._source_items() + [final])
