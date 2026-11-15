"""Чтение входных данных: тест-кейсы и общие проверки из yaml.

Источники:
    * `data/<case_id>.yaml`       — тест-кейсы (индивидуальные проверки);
    * `checks/common/<тип>.yaml`  — общие проверки по типу артефакта
      (ключи `checks` и `universal`).

Проверка в yaml — только структура (kind/metric/user_phrase/single_slide/
text); из неё сразу собирается объект `models.Check`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .config import CASES_DIR, COMMON_CHECKS_DIR
from .models import Check, TestCase


def makedirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


def normalize_artifact_type(raw: str) -> str:
    """Привести расширение артефакта к виду с точкой: pptx -> .pptx."""
    s = raw.strip().lower()
    if not s.startswith("."):
        s = "." + s
    return s


def vstr(v: Any) -> str:
    """Значение ячейки xlsx -> строка (числа и даты тоже)."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _flatten_check_text(text: Any) -> str:
    """Многострочный текст проверки из yaml -> одна строка."""
    return " ".join(ln.strip() for ln in str(text).splitlines() if ln.strip())


def _as_bool(value: Any, default: bool) -> bool:
    """Булево поле yaml-записи (True/False/yes/да/1/0...)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "да")


def check_from_entry(entry: Any, source: str) -> Check:
    """Запись проверки из yaml -> Check. Поддерживается только структура."""
    if not isinstance(entry, dict):
        raise ValueError(
            f"{source}: проверка должна быть структурой "
            "(kind/metric/user_phrase/single_slide/text), получено: "
            f"{type(entry).__name__}")
    kind = str(entry.get("kind") or "text").strip().lower()
    metric = str(entry.get("metric") or "").strip()
    return Check(
        text=_flatten_check_text(entry.get("text") or ""),
        kind=kind,
        metric=metric,
        use_user_phrase=_as_bool(entry.get("user_phrase"), True),
        single_slide=_as_bool(entry.get("single_slide"), False),
    )


def load_test_cases(cases_dir: Optional[str] = None) -> List[TestCase]:
    """Прочитать тест-кейсы из `data/*.yaml`.

    В `check_list` кейса попадают только индивидуальные проверки; общие по
    типу файла добавляются отдельно в `build_check_list`.
    """
    d = Path(cases_dir) if cases_dir else CASES_DIR
    if not d.is_dir():
        raise FileNotFoundError(f"Каталог тест-кейсов не найден: {d}")

    cases: List[TestCase] = []
    for path in sorted(d.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cid = str(raw.get("id") or path.stem).strip()
        if not cid:
            continue
        checks = [check_from_entry(c, str(path)) for c in (raw.get("checks") or [])]
        cases.append(
            TestCase(
                id=cid,
                user_phrase=str(raw.get("user_phrase") or "").strip(),
                skill=str(raw.get("skill") or "").strip(),
                artifact_type=normalize_artifact_type(str(raw.get("artifact_type") or "")),
                check_list=checks,
            )
        )
    return cases


def load_common_checks(artifact_type: str) -> Dict[str, List[Check]]:
    """Прочитать общие проверки типа файла из `checks/common/<тип>.yaml`.

    Возвращает {"checks": [...], "universal": [...]}: `checks` добавляются в
    чек-лист каждого кейса, `universal` — дефолт для `--validate` и добавка
    при полном прогоне.
    """
    name = normalize_artifact_type(artifact_type).lstrip(".")
    path = COMMON_CHECKS_DIR / f"{name}.yaml"
    out: Dict[str, List[Check]] = {"checks": [], "universal": []}
    if not path.exists():
        return out
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in out:
        out[key] = [
            check_from_entry(x, f"{path}#{key}")
            for x in (raw.get(key) or [])
        ]
    return out


def build_check_list(case: TestCase, include_universal: bool = False) -> List[Check]:
    """Собрать итоговый чек-лист кейса: общие по типу файла + индивидуальные.

    При `include_universal=True` добавляется универсальный блок; дубликаты
    (совпадают все поля проверки) отбрасываются.
    """
    common = load_common_checks(case.artifact_type)
    checks = list(common["checks"]) + list(case.check_list)
    if include_universal:
        seen = {
            (c.kind, c.metric, c.use_user_phrase, c.single_slide, c.text)
            for c in checks
        }
        checks += [
            c for c in common["universal"]
            if (c.kind, c.metric, c.use_user_phrase, c.single_slide, c.text) not in seen
        ]
    return checks