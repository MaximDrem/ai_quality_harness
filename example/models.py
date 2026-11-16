"""Модели кейсов, артефактов, проверок и результатов прогона.

Artifact объединяет файлы и финальное сообщение, хранит лениво вычисляемые
text/raw_structure/rendered_photos, связанные tool_calls и результаты проверок. ArtifactContext
выполняет выбор по ролям и материализацию атрибутов через процессоры.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class AttachmentSpec:
    path: str
    name: str = ""


@dataclass
class ArtifactSpec:
    name: str
    artifact_type: str = ""
    path: str = ""
    stage: str = "final"
    required: bool = True
    processor: str = ""
    kind: str = "file"


@dataclass
class Artifact:
    path: Optional[str]
    role: str
    name: str = ""
    artifact_type: str = ""
    origin_path: str = ""
    event_index: Optional[int] = None
    processor: str = ""
    kind: str = "file"
    text: str = ""
    raw_structure: Any = None
    rendered_photos: List[str] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    results: List["CheckResult"] = field(default_factory=list)
    loaded_attributes: set = field(default_factory=set, repr=False)
    render_dir: Optional[str] = field(default=None, repr=False)


CollectedArtifact = Artifact


@dataclass
class ArtifactContext:
    items: List[Artifact] = field(default_factory=list)

    @property
    def photos(self) -> Dict[str, List[str]]:
        return {
            item.path: item.rendered_photos
            for item in self.items
            if item.path is not None
        }

    def select(self, selectors: Optional[Sequence[str]] = None) -> List[Artifact]:
        selectors = list(selectors or ["final"])
        selected: List[Artifact] = []
        seen = set()
        for selector in selectors:
            role = "source" if selector == "attachments" else selector
            for item in self.items:
                matches = item.role == role or item.name == selector
                key = item.path if item.path is not None else f"{item.role}:{item.name}"
                if matches and key not in seen:
                    selected.append(item)
                    seen.add(key)
        return selected

    def paths(self, selectors: Optional[Sequence[str]] = None) -> List[str]:
        return [item.path for item in self.select(selectors) if item.path is not None]

    def photos_for(self, paths: Sequence[str]) -> List[str]:
        selected = [item for item in self.items if item.path in paths]
        return [photo for item in selected for photo in item.rendered_photos]

    def processor_for(self, artifact: str | Artifact, processor: str = ""):
        from .checks.processors import get_processor

        item = artifact if isinstance(artifact, Artifact) else next(
            (entry for entry in self.items if entry.path == str(artifact)),
            None,
        )
        path = item.path if item and item.path else str(artifact)
        name = processor or (item.processor if item else "")
        return get_processor(name=name, path=path, artifact_type=item.artifact_type if item else "")

    def materialize(
        self,
        artifacts: Sequence[Artifact],
        attribute: str,
        processor: str = "",
    ) -> None:
        for artifact in artifacts:
            if attribute in ("path", "tool_calls", "final_message") or attribute in artifact.loaded_attributes:
                continue
            if artifact.kind == "message":
                artifact.loaded_attributes.add(attribute)
                continue
            self.processor_for(artifact, processor).process(artifact, {attribute})

    def extract_text(self, artifact: str | Artifact, processor: str = "") -> str:
        item = artifact if isinstance(artifact, Artifact) else next(
            entry for entry in self.items if entry.path == str(artifact)
        )
        self.materialize([item], "text", processor)
        return item.text

    def serialize_raw_structure(self, artifact: Artifact, processor: str = "") -> str:
        self.materialize([artifact], "raw_structure", processor)
        if artifact.kind == "message":
            return json.dumps(artifact.raw_structure, ensure_ascii=False, default=str)
        return self.processor_for(artifact, processor).serialize_raw_structure(artifact.raw_structure)


@dataclass
class TestCase:
    id: str
    user_phrase: str
    skill: str
    artifact_type: str
    check_list: List["Check"] = field(default_factory=list)
    scenario: str = ""
    case_dir: str = ""
    metric_groups: List[str] = field(default_factory=list)
    attachments: List[AttachmentSpec] = field(default_factory=list)
    artifacts: List[ArtifactSpec] = field(default_factory=list)
    processor: str = ""


@dataclass
class Check:
    text: str
    kind: str = "llm"
    metric: str = ""
    use_user_phrase: bool = True
    single_slide: bool = False
    mode: str = "serial"
    handler: str = ""
    artifacts: List[str] = field(default_factory=list)
    options: Dict[str, Any] = field(default_factory=dict)
    processor: str = ""
    weight: Optional[float] = None
    input: str = ""


@dataclass
class CheckResult:
    check: Check
    success: bool
    reason: str = ""
    mode: str = "serial"
    source: str = ""
    error: str = ""
    artifact_path: Optional[str] = None
    input_paths: List[str] = field(default_factory=list)
    input_attribute: str = ""


@dataclass
class MetricResult:
    name: str
    success: bool
    total: int = 0
    passed: int = 0


@dataclass
class RunResult:
    test_case: TestCase
    run: int = 1
    skills: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    render_photos: List[str] = field(default_factory=list)
    questions: List[Dict[str, Any]] = field(default_factory=list)
    check_results: List[CheckResult] = field(default_factory=list)
    dropped: bool = False
    drop_reason: str = ""
    judge_xlsx: str = ""
    agent_time_sec: float = 0.0
    tokens: Dict[str, int] = field(default_factory=dict)
    continuations: int = 0
    intermediate_artifacts: List[str] = field(default_factory=list)
    source_artifacts: List[str] = field(default_factory=list)
    final_message: str = ""
    actual_skill: str = ""
    routing_success: bool = False
    routing_applicable: bool = True
    context_tokens: int = 0
    context_limit: int = 0
    context_percent: float = 0.0
