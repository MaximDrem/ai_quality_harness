"""Data-модели пайплайна: кейс, проверка, результаты.

Модели — чистые dataclass'ы без логики; чтение yaml — в `cases`.
"""
from __future__ import annotations

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


@dataclass
class CollectedArtifact:
    path: str
    role: str
    name: str = ""
    artifact_type: str = ""
    origin_path: str = ""
    event_index: Optional[int] = None
    processor: str = ""


@dataclass
class ArtifactContext:
    items: List[CollectedArtifact] = field(default_factory=list)
    photos: Dict[str, List[str]] = field(default_factory=dict)

    def select(self, selectors: Optional[Sequence[str]] = None) -> List[CollectedArtifact]:
        selectors = list(selectors or ["final"])
        selected: List[CollectedArtifact] = []
        seen = set()
        for selector in selectors:
            role = "source" if selector == "attachments" else selector
            for item in self.items:
                matches = item.role == role or item.name == selector
                if matches and item.path not in seen:
                    selected.append(item)
                    seen.add(item.path)
        return selected

    def paths(self, selectors: Optional[Sequence[str]] = None) -> List[str]:
        return [item.path for item in self.select(selectors)]

    def photos_for(self, paths: Sequence[str]) -> List[str]:
        return [photo for path in paths for photo in self.photos.get(path, [])]

    def processor_for(self, artifact: str | CollectedArtifact, processor: str = ""):
        from .processors import get_processor

        item = artifact if isinstance(artifact, CollectedArtifact) else next(
            (entry for entry in self.items if entry.path == str(artifact)),
            None,
        )
        path = item.path if item else str(artifact)
        name = processor or (item.processor if item else "")
        return get_processor(name=name, path=path)

    def extract_text(self, artifact: str | CollectedArtifact, processor: str = "") -> str:
        path = artifact.path if isinstance(artifact, CollectedArtifact) else str(artifact)
        return self.processor_for(artifact, processor).extract_text(path)


@dataclass
class TestCase:
    """Один тест-кейс из `data/<case_id>.yaml`."""

    id: str
    user_phrase: str
    skill: str
    artifact_type: str  # основной финальный тип для legacy-кейсов
    check_list: List[Check] = field(default_factory=list)  # индивидуальные проверки кейса
    scenario: str = ""
    case_dir: str = ""
    metric_groups: List[str] = field(default_factory=list)
    attachments: List[AttachmentSpec] = field(default_factory=list)
    artifacts: List[ArtifactSpec] = field(default_factory=list)
    processor: str = ""


@dataclass
class Check:
    """Одна проверка кейса (из yaml-структуры в `data/` или `checks/common/`)."""

    text: str                      # текст проверки
    kind: str = "text"             # "text" | "vlm" | "functional"
    metric: str = ""               # метка метрики
    use_user_phrase: bool = True   # передавать ли user_phrase кейса судье и в проверку
    single_slide: bool = False     # судить по одному слайду за запрос
    mode: str = "serial"           # режим запуска (всегда последовательный)
    handler: str = ""              # модуль функциональной проверки без example.checks.functional
    artifacts: List[str] = field(default_factory=list)  # final/source/intermediate или имена артефактов
    options: Dict[str, Any] = field(default_factory=dict)
    processor: str = ""


@dataclass
class CheckResult:
    """Результат одной проверки одним судьёй (llm/функциональным)."""

    check: Check
    success: bool                  # True — пройдена
    reason: str = ""               # обоснование
    mode: str = "serial"           # фактический режим запуска
    source: str = ""               # что отдано судье: текст/фото/—
    error: str = ""                # ошибка судьи, если была


@dataclass
class MetricResult:
    """Агрегированный результат метрики (группы проверок)."""

    name: str
    success: bool                  # пройдена, если ВСЕ проверки группы успешны
    total: int = 0
    passed: int = 0


@dataclass
class RunResult:
    """Сводный результат выполнения одного кейса."""

    test_case: TestCase
    skills: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    render_photos: List[str] = field(default_factory=list)
    questions: List[Dict[str, Any]] = field(default_factory=list)
    check_results: List[CheckResult] = field(default_factory=list)
    metric_results: List[MetricResult] = field(default_factory=list)
    run: int = 1                  # номер прогона датасета
    judge_xlsx: str = ""           # путь к итоговому xlsx
    dropped: bool = False          # кейс пропущен (уточняющий вопрос агента)
    drop_reason: str = ""          # причина пропуска
    agent_time_sec: float = 0.0    # время работы агента, сек
    tokens: Dict[str, int] = field(default_factory=dict)  # {"input","output","reasoning","total"}
    continuations: int = 0         # сколько раз отправлено «Продолжить»
    intermediate_artifacts: List[str] = field(default_factory=list)
    source_artifacts: List[str] = field(default_factory=list)