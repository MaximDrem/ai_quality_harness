"""Data-модели пайплайна: кейс, проверка, результаты.

Модели — чистые dataclass'ы без логики; чтение yaml — в `cases`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class TestCase:
    """Один тест-кейс из `data/<case_id>.yaml`."""

    id: str
    user_phrase: str
    skill: str
    artifact_type: str  # .pptx / .docx / .xlsx / .pdf
    check_list: List[Check] = field(default_factory=list)  # индивидуальные проверки кейса


@dataclass
class Check:
    """Одна проверка кейса (из yaml-структуры в `data/` или `checks/common/`)."""

    text: str                      # текст проверки
    kind: str = "text"             # "text" | "vlm" | "functional"
    metric: str = ""               # метка метрики
    use_user_phrase: bool = True   # передавать ли user_phrase кейса судье и в проверку
    single_slide: bool = False     # судить по одному слайду за запрос
    mode: str = "serial"           # режим запуска (всегда последовательный)


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