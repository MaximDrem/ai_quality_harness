"""Функциональная проверка `file_renderable`: файл существует и рендерится.

Валидность файла определяется по наличию отрендеренных PNG (фото страниц/
слайдов): если рендер не дал ни одного изображения — файл не открывается.

Единый интерфейс функциональной проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

from typing import List, Optional

from ...models import Check, CheckResult
from ._helpers import ok, unavailable


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """Проверка валидности файла: существует и открывается/рендерится (есть PNG)."""
    if not artifacts:
        return unavailable(check, "Артефакт не создан агентом (файл не найден)", artifacts, photos, user_phrase)
    if not photos:
        return unavailable(check, "Артефакт не удалось открыть/отрендерить (нет PNG)", artifacts, photos, user_phrase)
    return ok(check, "Файл существует и открывается (есть PNG)", artifacts, photos, user_phrase)