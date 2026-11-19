"""Профиль judge для сценария клонирования презентации."""

from ..presentation import (
    JudgeResult,
    SYSTEM_PROMPT,
    build_text_prompt,
    build_vision_prompt,
)

__all__ = ["SYSTEM_PROMPT", "build_text_prompt", "build_vision_prompt", "JudgeResult"]
