"""Динамическая загрузка и выполнение функциональных проверок Artifact.

Handler выбирается из YAML без глобального реестра. Сегмент path, text,
raw_structure, rendered_photos или tool_calls определяет атрибут; REQUIRES
добавляет остальные. Результат прикрепляется к основному артефакту.
"""

from __future__ import annotations

import functools
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Callable, Dict, List

from ...models import ArtifactContext, Check, CheckResult, TestCase
from .._helpers import unavailable

_ROOT = Path(__file__).resolve().parent
_ATTRIBUTES = {"path", "text", "raw_structure", "rendered_photos", "tool_calls"}
_ALIASES = {
    "file_opens": "file_renderable",
    "render": "file_renderable",
    "render_success": "file_renderable",
}


@functools.lru_cache(maxsize=1)
def _handler_index() -> Dict[str, Path]:
    index: Dict[str, Path] = {}
    stems: Dict[str, Path] = {}
    ambiguous = set()
    for path in _ROOT.rglob("*.py"):
        if path.name.startswith("_") or path == Path(__file__):
            continue
        relative = path.relative_to(_ROOT).with_suffix("").as_posix()
        index[relative] = path
        if path.stem in ambiguous:
            continue
        if path.stem in stems and stems[path.stem] != path:
            index.pop(path.stem, None)
            ambiguous.add(path.stem)
            continue
        stems[path.stem] = path
        index[path.stem] = path
    return index


def clear_handler_cache() -> None:
    _handler_index.cache_clear()


def _load_handler(name: str) -> Callable:
    normalized = name.strip().replace("\\", "/").removesuffix(".py")
    normalized = _ALIASES.get(normalized, normalized)
    paths = _handler_index()
    path = paths.get(normalized)
    if path is None:
        raise ModuleNotFoundError(f"Функциональная проверка не найдена: {normalized}")
    relative = path.relative_to(_ROOT).with_suffix("").as_posix()
    module_name = "example.checks.functional." + relative.replace("/", ".")
    if module_name in sys.modules:
        module = sys.modules[module_name]
    else:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Не удалось загрузить проверку: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    handler = getattr(module, "run", None)
    if not callable(handler):
        raise TypeError(f"В модуле {path} нет callable run(...)")
    return handler


def _input_attribute(name: str) -> str:
    normalized = name.strip().replace("\\", "/").removesuffix(".py")
    normalized = _ALIASES.get(normalized, normalized)
    parts = normalized.split("/")
    attribute = next((part for part in parts if part in _ATTRIBUTES), None)
    if attribute is None:
        raise ValueError(
            f"Handler {name!r} должен содержать каталог атрибута {sorted(_ATTRIBUTES)}"
        )
    return attribute


def run_functional_checks(case: TestCase, context: ArtifactContext) -> List[CheckResult]:
    return [
        run_functional_check(check, context, case.user_phrase)
        for check in case.check_list
        if check.kind == "function"
    ]


def run_functional_check(check: Check, context: ArtifactContext, user_phrase: str = "") -> CheckResult:
    selected = context.select(check.artifacts or ["final"])
    phrase = user_phrase if check.use_user_phrase else None
    name = check.handler or (check.text or "").strip().split(None, 1)[0]
    try:
        attribute = _input_attribute(name)
        handler = _load_handler(name)
        module = sys.modules[handler.__module__]
        required = {attribute} | set(getattr(module, "REQUIRES", set()))
        if not context.processed:
            raise ValueError("ArtifactContext должен быть обработан processors до checks")
        for required_attribute in required:
            if required_attribute in {"path", "tool_calls"}:
                continue
            for artifact in selected:
                error = artifact.processing_errors.get(required_attribute) or artifact.processing_errors.get("processor")
                if error:
                    raise ValueError(f"{artifact.name}: {required_attribute}: {error}")
    except Exception as error:
        return unavailable(
            check,
            f"Функциональная проверка {name!r} недоступна: {error}",
            selected,
            None,
        )
    parameters = inspect.signature(handler).parameters
    kwargs = {"context": context} if "context" in parameters else {}
    if "photos" in parameters:
        photos = [photo for artifact in selected for photo in artifact.rendered_photos]
        result = handler(check, selected, photos, phrase, **kwargs)
    else:
        result = handler(check, selected, phrase, **kwargs)
    result.input_attribute = attribute
    result.input_paths = [artifact.path for artifact in selected if artifact.path]
    if selected:
        primary = selected[-1]
        result.artifact_path = primary.path
        primary.results.append(result)
    return result
