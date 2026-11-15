"""Функциональные проверки артефактов, считаемые кодом (без судьи-LLM).

Каждая проверка — скрипт `<имя>.py` с интерфейсом
`run(check, artifacts, photos, user_phrase) -> CheckResult`; реестр — `_HANDLERS`.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ...models import Check, CheckResult, TestCase
from . import (
    content_variety,
    empty_text_frame,
    escaped_newlines,
    file_exists,
    file_renderable,
    font_variety,
    image_coverage,
    repeated_words,
    shape_density,
    slide_count,
    slide_fullness,
    spelling,
    style_consistency,
    text_background_contrast,
    text_hierarchy,
)
from ._helpers import unavailable

_RENDER_KEYWORDS = ("рендери", "открыва", "фото", "render", "страниц")


def run_functional_checks(case: TestCase, artifacts: List[str], photos: List[str]) -> List[CheckResult]:
    """Все функциональные проверки кейса из его чек-листа."""
    results: List[CheckResult] = []
    for check in case.check_list:
        if check.kind == "functional":
            user_phrase = case.user_phrase if check.use_user_phrase else None
            results.append(run_functional_check(check, artifacts, photos, user_phrase))
    return results


def run_functional_check(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """Маршрутизатор: по имени проверки выбрать хэндлер из `_HANDLERS`."""
    name = (check.text or "").strip().lower()
    handler = _pick_handler(name)
    return handler(check, artifacts, photos, user_phrase)


def _pick_handler(name: str) -> Callable:
    if name in _HANDLERS:
        return _HANDLERS[name]
    # Проверка с параметром: «slide_count 7» -> «slide_count».
    head = name.split(None, 1)[0] if name else ""
    if head in _HANDLERS:
        return _HANDLERS[head]
    if any(k in name for k in _RENDER_KEYWORDS):
        return file_renderable.run
    return check_not_implemented


def check_not_implemented(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """Неизвестная функциональная проверка — сообщаем, что не реализована."""
    name = (check.text or "").strip()
    return unavailable(
        check,
        f"Функциональная проверка не реализована в коде: {name!r}",
        artifacts, photos, user_phrase,
    )


# Реестр проверок: имя -> run(...) скрипта <имя>.py. Синонимы указывают на
# тот же скрипт.
_HANDLERS: Dict[str, Callable] = {
    "file_exists": file_exists.run,
    "file_renderable": file_renderable.run,
    "file_opens": file_renderable.run,
    "render": file_renderable.run,
    "render_success": file_renderable.run,
    "empty_text_frame": empty_text_frame.run,
    "text_background_contrast": text_background_contrast.run,
    "style_consistency": style_consistency.run,
    "slide_count": slide_count.run,
    "repeated_words": repeated_words.run,
    "escaped_newlines": escaped_newlines.run,
    "image_coverage": image_coverage.run,
    "font_variety": font_variety.run,
    "slide_fullness": slide_fullness.run,
    "content_variety": content_variety.run,
    "shape_density": shape_density.run,
    "text_hierarchy": text_hierarchy.run,
    "spelling": spelling.run,
}