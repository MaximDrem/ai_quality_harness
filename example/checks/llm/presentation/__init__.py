"""Профиль judge для сценариев создания презентаций."""

from .prompts import SYSTEM_PROMPT, build_text_prompt, build_vision_prompt
from .schema import JudgeResult

__all__ = ["SYSTEM_PROMPT", "build_text_prompt", "build_vision_prompt", "JudgeResult"]
