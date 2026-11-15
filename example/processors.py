from __future__ import annotations

import functools
import importlib.util
import sys
from pathlib import Path
from typing import Dict

_ROOT = Path(__file__).resolve().parent / "checks" / "functional"


@functools.lru_cache(maxsize=1)
def _processor_paths() -> Dict[str, Path]:
    result: Dict[str, Path] = {}
    for path in _ROOT.glob("*/processor.py"):
        result[path.parent.name] = path
    return result


def _processor_class(name: str):
    paths = _processor_paths()
    if name not in paths:
        available = ", ".join(sorted(paths)) or "—"
        raise ValueError(f"Процессор {name!r} не найден. Доступные: {available}")
    path = paths[name]
    module_name = f"example.checks.functional.{name}.processor"
    if module_name in sys.modules:
        module = sys.modules[module_name]
    else:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Не удалось загрузить процессор: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    processor_class = getattr(module, "Processor", None) or getattr(module, "ArtifactProcessor", None)
    if processor_class is None:
        raise TypeError(f"В процессоре {path} нет класса Processor")
    return processor_class


@functools.lru_cache(maxsize=None)
def _processor_instance(name: str):
    return _processor_class(name)()


def get_processor(name: str = "", path: str = ""):
    normalized = name.strip().replace("\\", "/").removesuffix(".py").strip("/")
    if normalized.endswith("/processor"):
        normalized = normalized.rsplit("/", 1)[0]
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    if normalized:
        return _processor_instance(normalized)
    suffix = Path(path).suffix.lower()
    for candidate in sorted(_processor_paths()):
        processor = _processor_instance(candidate)
        if suffix in {item.lower() for item in processor.extensions}:
            return processor
    raise ValueError(f"Нет процессора для файла {path!r} с расширением {suffix!r}")


def clear_processor_cache() -> None:
    _processor_paths.cache_clear()
    _processor_instance.cache_clear()
