from __future__ import annotations

import functools
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ...models import ArtifactContext, Check, CheckResult, TestCase
from ._helpers import unavailable

_ROOT = Path(__file__).resolve().parent
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
        if path.name.startswith("_") or path.name == "processor.py" or path == Path(__file__):
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


def _selected_inputs(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    context: Optional[ArtifactContext],
) -> tuple[List[str], List[str]]:
    if context is None:
        return artifacts, photos
    selected = context.paths(check.artifacts or ["final"])
    return selected, context.photos_for(selected)


def run_functional_checks(
    case: TestCase,
    artifacts: List[str],
    photos: List[str],
    context: Optional[ArtifactContext] = None,
) -> List[CheckResult]:
    results: List[CheckResult] = []
    for check in case.check_list:
        if check.kind != "functional":
            continue
        results.append(run_functional_check(check, artifacts, photos, case.user_phrase, context))
    return results


def run_functional_check(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
    context: Optional[ArtifactContext] = None,
) -> CheckResult:
    selected_artifacts, selected_photos = _selected_inputs(check, artifacts, photos, context)
    phrase = user_phrase if check.use_user_phrase else None
    name = check.handler or (check.text or "").strip().split(None, 1)[0]
    try:
        handler = _load_handler(name)
    except (ImportError, TypeError, ValueError) as error:
        return unavailable(
            check,
            f"Функциональная проверка {name!r} недоступна: {error}",
            selected_artifacts,
            selected_photos,
            phrase,
        )
    kwargs = {}
    if "context" in inspect.signature(handler).parameters:
        kwargs["context"] = context
    return handler(check, selected_artifacts, selected_photos, phrase, **kwargs)
