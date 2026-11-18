"""Загрузка сценариев, YAML-кейсов и групп метрик.

Кейсы читаются из data/<scenario>/, группы — из metrics/<scenario>/.
Собранный чек-лист содержит явно выбранные группы и индивидуальные проверки
с наследованием processor и weight.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .config import CASES_DIR, METRICS_DIR
from .models import ArtifactSpec, AttachmentSpec, Check, TestCase

DEFAULT_SCENARIO = "presentations"


def makedirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def normalize_artifact_type(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value and not value.startswith("."):
        value = "." + value
    return value


def vstr(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def available_scenarios(data_root: Optional[Path] = None) -> List[str]:
    root = Path(data_root) if data_root else CASES_DIR
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and any(path.glob("*.yaml"))
    )


def resolve_cases_dir(
    scenario: Optional[str] = None,
    cases_dir: Optional[str] = None,
) -> Tuple[Path, str]:
    if cases_dir:
        path = Path(cases_dir).expanduser().resolve()
        return path, scenario or path.name
    name = scenario or DEFAULT_SCENARIO
    return CASES_DIR / name, name


def _flatten_check_text(text: Any) -> str:
    return " ".join(line.strip() for line in str(text or "").splitlines() if line.strip())


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "да")


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        return [str(value).strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _as_weight(value: Any, source: str) -> Optional[float]:
    if value is None:
        return None
    try:
        weight = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{source}: weight должен быть числом") from error
    if weight < 0:
        raise ValueError(f"{source}: weight не может быть отрицательным")
    return weight


def check_from_entry(entry: Any, source: str) -> Check:
    if not isinstance(entry, dict):
        raise ValueError(f"{source}: проверка должна быть YAML-структурой")
    options = entry.get("options") or {}
    if not isinstance(options, dict):
        raise ValueError(f"{source}: options проверки должны быть структурой")
    kind = str(entry.get("kind") or "llm").strip().lower()
    input_name = str(entry.get("input") or "").strip()
    if kind not in ("llm", "function"):
        raise ValueError(f"{source}: kind должен быть llm или function")
    if kind == "llm" and input_name not in ("text", "raw_structure", "rendered_photos", "tool_call"):
        raise ValueError(
            f"{source}: для llm input обязателен: text/raw_structure/rendered_photos/tool_call"
        )
    return Check(
        text=_flatten_check_text(entry.get("text")),
        kind=kind,
        metric=str(entry.get("metric") or "").strip(),
        use_user_phrase=_as_bool(entry.get("user_phrase"), True),
        single_slide=_as_bool(entry.get("single_slide"), False),
        handler=str(entry.get("handler") or "").strip(),
        artifacts=_as_str_list(entry.get("artifacts")),
        options=dict(options),
        processor=str(entry.get("processor") or "").strip(),
        weight=_as_weight(entry.get("weight"), source),
        input=input_name,
    )


def _attachment_from_entry(entry: Any, source: str) -> AttachmentSpec:
    if isinstance(entry, str):
        path = entry.strip()
        return AttachmentSpec(path=path, name=Path(path).stem)
    if not isinstance(entry, dict) or not str(entry.get("path") or "").strip():
        raise ValueError(f"{source}: вложение должно быть путём или структурой с path")
    path = str(entry["path"]).strip()
    return AttachmentSpec(
        path=path,
        name=str(entry.get("name") or Path(path).stem).strip(),
    )


def _artifact_from_entry(entry: Any, source: str, index: int) -> ArtifactSpec:
    if not isinstance(entry, dict):
        raise ValueError(f"{source}: артефакт должен быть YAML-структурой")
    stage = str(entry.get("stage") or "final").strip().lower()
    if stage not in ("final", "intermediate"):
        raise ValueError(f"{source}: stage артефакта должен быть final или intermediate")
    name = str(entry.get("name") or f"{stage}_{index}").strip()
    kind = str(entry.get("kind") or "file").strip().lower()
    if kind not in ("file", "message"):
        raise ValueError(f"{source}: kind артефакта должен быть file или message")
    raw_type = str(entry.get("type") or entry.get("artifact_type") or "")
    artifact_path = str(entry.get("path") or "").strip()
    if kind == "file" and not raw_type and not artifact_path:
        raise ValueError(f"{source}: файловый артефакт должен объявлять type или path")
    return ArtifactSpec(
        name=name,
        artifact_type=normalize_artifact_type(raw_type) if kind == "file" else raw_type,
        path=artifact_path,
        stage=stage,
        required=_as_bool(entry.get("required"), True),
        processor=str(entry.get("processor") or "").strip(),
        kind=kind,
    )


def load_test_cases(
    cases_dir: Optional[str] = None,
    scenario: Optional[str] = None,
) -> List[TestCase]:
    directory, scenario_name = resolve_cases_dir(scenario, cases_dir)
    if not directory.is_dir():
        available = ", ".join(available_scenarios()) or "—"
        raise FileNotFoundError(
            f"Каталог сценария не найден: {directory}. Доступные сценарии: {available}"
        )

    cases: List[TestCase] = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        case_id = str(raw.get("id") or path.stem).strip()
        if not case_id:
            continue
        checks = [check_from_entry(item, str(path)) for item in (raw.get("checks") or [])]
        attachments = [
            _attachment_from_entry(item, str(path))
            for item in (raw.get("attachments") or [])
        ]
        artifacts = [
            _artifact_from_entry(item, str(path), index)
            for index, item in enumerate(raw.get("artifacts") or [], start=1)
        ]
        artifact_type = normalize_artifact_type(str(raw.get("artifact_type") or ""))
        if not artifact_type:
            final = next((item for item in artifacts if item.stage == "final"), None)
            artifact_type = final.artifact_type if final else ""
        cases.append(
            TestCase(
                id=case_id,
                user_phrase=str(raw.get("user_phrase") or "").strip(),
                skill=str(raw.get("skill") or scenario_name).strip(),
                artifact_type=artifact_type,
                check_list=checks,
                scenario=scenario_name,
                case_dir=str(directory),
                metric_groups=_as_str_list(raw.get("metric_groups") or raw.get("metrics")),
                attachments=attachments,
                artifacts=artifacts,
                processor=str(raw.get("processor") or "").strip(),
            )
        )
    return cases


def _metric_file(root: Path, name: str) -> Path:
    relative = Path(f"{name}.yaml")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Некорректное имя группы метрик: {name!r}")
    return root / relative


def _load_metric_group(case: TestCase, name: str) -> Tuple[str, str, List[Check]]:
    scenario_root = METRICS_DIR / case.scenario
    path = _metric_file(scenario_root, name)
    if not path.is_file() and case.artifact_type:
        common_root = METRICS_DIR / "common" / case.artifact_type.lstrip(".")
        path = _metric_file(common_root, name)
    if not path.is_file():
        raise FileNotFoundError(
            f"Группа метрик {name!r} кейса {case.id} не найдена: {path}"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if isinstance(raw, list):
        raw = {"checks": raw}
    phase = str(raw.get("phase") or "universal").strip().lower()
    if phase not in ("common", "universal"):
        raise ValueError(f"{path}: phase должен быть common или universal")
    processor = str(raw.get("processor") or "").strip()
    weight = _as_weight(raw.get("weight"), str(path))
    checks = [
        check_from_entry(item, f"{path}#checks")
        for item in (raw.get("checks") or [])
    ]
    checks = [
        replace(
            check,
            processor=check.processor or processor,
            weight=check.weight if check.weight is not None else weight,
        )
        for check in checks
    ]
    return phase, processor, checks


def _check_identity(check: Check) -> str:
    return json.dumps(
        {
            "kind": check.kind,
            "metric": check.metric,
            "use_user_phrase": check.use_user_phrase,
            "single_slide": check.single_slide,
            "text": check.text,
            "handler": check.handler,
            "artifacts": check.artifacts,
            "options": check.options,
            "processor": check.processor,
            "weight": check.weight,
            "input": check.input,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def build_check_list(
    case: TestCase,
    include_universal: bool = True,
    include_common: bool = True,
) -> List[Check]:
    common: List[Check] = []
    universal: List[Check] = []
    for name in case.metric_groups:
        phase, _processor, checks = _load_metric_group(case, name)
        target = common if phase == "common" else universal
        target.extend(checks)
    metric_weights: Dict[str, float] = {}
    for check in common + universal:
        if check.metric and check.weight is not None:
            if check.metric in metric_weights and metric_weights[check.metric] != check.weight:
                raise ValueError(f"Конфликт weight для метрики {check.metric!r}")
            metric_weights[check.metric] = check.weight
    individual = [
        replace(
            check,
            processor=check.processor or case.processor,
            weight=(
                metric_weights.get(check.metric)
                if check.weight is None
                else check.weight
            ),
        )
        for check in case.check_list
    ]
    result = (common if include_common else []) + individual
    if include_universal:
        result += universal
    unique: List[Check] = []
    seen = set()
    for check in result:
        identity = _check_identity(check)
        if identity not in seen:
            unique.append(check)
            seen.add(identity)
    return unique


def load_common_checks(artifact_type: str) -> Dict[str, List[Check]]:
    result: Dict[str, List[Check]] = {"checks": [], "universal": []}
    root = METRICS_DIR / "common" / normalize_artifact_type(artifact_type).lstrip(".")
    if not root.is_dir():
        return result
    for path in sorted(root.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        phase = "checks" if raw.get("phase") == "common" else "universal"
        result[phase].extend(
            check_from_entry(item, str(path)) for item in (raw.get("checks") or [])
        )
    return result


def resolve_attachment_paths(case: TestCase) -> List[str]:
    paths: List[str] = []
    for attachment in case.attachments:
        path = Path(attachment.path).expanduser()
        if not path.is_absolute():
            path = Path(case.case_dir) / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Вложение кейса {case.id} не найдено: {path}")
        paths.append(str(path))
    return paths


def artifact_specs(case: TestCase, stage: Optional[str] = None) -> List[ArtifactSpec]:
    specs = [
        replace(spec, processor=spec.processor or case.processor)
        for spec in case.artifacts
    ]
    if not specs and case.artifact_type and stage in (None, "final"):
        specs = [ArtifactSpec(name="final", artifact_type=case.artifact_type, processor=case.processor)]
    if not specs and any("final_message" in check.artifacts for check in case.check_list):
        specs = [ArtifactSpec(name="final_message", stage="final", kind="message")]
    if stage is not None:
        return [spec for spec in specs if spec.stage == stage]
    return specs


def validate_case_output(case: TestCase) -> None:
    if artifact_specs(case, "final"):
        return
    raise ValueError(
        f"Кейс {case.id} не объявляет финальный результат. Укажите artifact_type, "
        "файл в artifacts или final_message с kind: message."
    )
