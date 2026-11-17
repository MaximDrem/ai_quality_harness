"""Динамический выбор judge-профиля по имени сценария.

Каждый пакет checks/llm/<scenario>/ экспортирует SYSTEM_PROMPT,
build_text_prompt, build_vision_prompt и JudgeResult. По умолчанию и для
сценария presentations используется профиль presentation.
"""

from __future__ import annotations

import functools
import importlib
import re

_SCENARIO_ALIASES = {"": "presentation", "presentation": "presentation", "presentations": "presentation"}


@functools.lru_cache(maxsize=None)
def get_llm_profile(scenario: str = "presentation"):
    normalized = (scenario or "presentation").strip()
    normalized = _SCENARIO_ALIASES.get(normalized, normalized)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
        raise ValueError(f"Некорректное имя LLM-профиля: {normalized!r}")
    try:
        profile = importlib.import_module(f".{normalized}", __name__)
    except ModuleNotFoundError as error:
        raise ValueError(f"LLM-профиль сценария {normalized!r} не найден") from error
    required = ("SYSTEM_PROMPT", "build_text_prompt", "build_vision_prompt", "JudgeResult")
    missing = [name for name in required if not hasattr(profile, name)]
    if missing:
        raise TypeError(f"У LLM-профиля {normalized!r} отсутствуют: {', '.join(missing)}")
    return profile


def clear_llm_profile_cache() -> None:
    get_llm_profile.cache_clear()


__all__ = ["get_llm_profile", "clear_llm_profile_cache"]
