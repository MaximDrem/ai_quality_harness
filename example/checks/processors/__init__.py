"""Динамическая фабрика и eager-обработка ArtifactContext.

До checks каждый файловый Artifact направляется в явно заданный processor или
в processor, объявивший поддержку его расширения. Конкретный processor сразу
заполняет все свои атрибуты в том же Artifact.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import sys
from pathlib import Path
from typing import Dict

_ROOT = Path(__file__).resolve().parent


@functools.lru_cache(maxsize=1)
def _processor_paths() -> Dict[str, Path]:
    return {
        path.stem.removeprefix("processor_"): path
        for path in _ROOT.glob("processor_*.py")
    }


def _processor_class(name: str):
    paths = _processor_paths()
    if name not in paths:
        available = ", ".join(sorted(paths)) or "—"
        raise ValueError(f"Процессор {name!r} не найден. Доступные: {available}")
    path = paths[name]
    module_name = f"example.checks.processors.processor_{name}"
    if module_name in sys.modules:
        module = sys.modules[module_name]
    else:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Не удалось загрузить процессор: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    processor_class = getattr(module, "Processor", None)
    if processor_class is None:
        raise TypeError(f"В процессоре {path} нет класса Processor")
    return processor_class


@functools.lru_cache(maxsize=None)
def _processor_instance(name: str):
    return _processor_class(name)()


def get_processor(name: str = "", path: str = "", artifact_type: str = ""):
    normalized = name.strip().removesuffix(".py")
    if normalized.startswith("processor_"):
        normalized = normalized.removeprefix("processor_")
    if normalized:
        return _processor_instance(normalized)
    suffix = (artifact_type or Path(path).suffix).lower()
    for candidate in sorted(_processor_paths()):
        processor = _processor_instance(candidate)
        if suffix in {item.lower() for item in processor.extensions}:
            return processor
    raise ValueError(f"Нет процессора для типа {suffix!r} файла {path!r}")


def process_context(context) -> None:
    for artifact in context.items:
        artifact.processing_errors = {}
        if artifact.kind == "message":
            artifact.serialized_raw_structure = json.dumps(
                artifact.raw_structure,
                ensure_ascii=False,
                default=str,
            )
            continue
        try:
            processor = get_processor(
                name=artifact.processor,
                path=artifact.path or "",
                artifact_type=artifact.artifact_type,
            )
            processor.process(artifact)
        except Exception as error:  # noqa: BLE001
            artifact.processing_errors["processor"] = str(error)
    context.processed = True


def clear_processor_cache() -> None:
    _processor_paths.cache_clear()
    _processor_instance.cache_clear()
