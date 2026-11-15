"""Функциональная проверка `slide_count`: число слайдов в колоде.

Считает слайды .pptx (python-pptx) и сверяет с условием из текста проверки.

Строгий формат условия (только символы, без слов «больше»/«не менее»):

    slide_count 7      — ровно 7 (по умолчанию)
    slide_count =7     — ровно 7 (можно ==7)
    slide_count >7     — больше 7
    slide_count <7     — меньше 7
    slide_count >=7    — не менее 7
    slide_count <=7    — не более 7
    slide_count 5-7    — от 5 до 7 включительно

Единый интерфейс проверки:
    run(check, artifacts, photos, user_phrase) -> CheckResult
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from pptx import Presentation

from ...models import Check, CheckResult
from ._helpers import ok, unavailable

# Строгая форма: `slide_count [оп] N` или `slide_count N-M`.
_COND_RE = re.compile(
    r"^slide_count\s+(?:(>=|<=|>|<|==|=)\s*(\d+)|(\d+)\s*-\s*(\d+)|(\d+))$"
)

_OPS: dict[str, Tuple[Callable[[int, int], bool], str]] = {
    ">": (lambda a, b: a > b, "больше"),
    "<": (lambda a, b: a < b, "меньше"),
    "=": (lambda a, b: a == b, "ровно"),
    "==": (lambda a, b: a == b, "ровно"),
    ">=": (lambda a, b: a >= b, "не менее"),
    "<=": (lambda a, b: a <= b, "не более"),
}


def _parse_condition(check: Check) -> Optional[Tuple[Callable[[int], bool], str]]:
    """Разобрать условие из текста проверки.

    Возвращает (предикат, описание условия для reason) или None, если
    текст не соответствует строгому формату.
    """
    m = _COND_RE.match((check.text or "").strip())
    if not m:
        return None
    op, n_op, lo, hi, n_plain = m.groups()
    if lo is not None:
        lo_i, hi_i = int(lo), int(hi)
        if lo_i > hi_i:
            return None
        return (lambda a, lo=lo_i, hi=hi_i: lo <= a <= hi), \
            f"в диапазоне {lo_i}–{hi_i}"
    n = int(n_op or n_plain)
    cmp_fn, op_word = _OPS[op or "="]
    return (lambda a, n=n, f=cmp_fn: f(a, n)), f"{op_word} {n}"


def run(
    check: Check,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str] = None,
) -> CheckResult:
    """PASS, если число слайдов колоды удовлетворяет условию."""
    pptx = next((a for a in artifacts if Path(a).suffix.lower() == ".pptx"), None)
    if not pptx:
        return unavailable(check, "Нет .pptx-артефакта для проверки", artifacts, photos, user_phrase)
    cond = _parse_condition(check)
    if cond is None:
        return unavailable(
            check,
            "Некорректное условие проверки (строгий формат: "
            "`slide_count [>|<|>=|<=|=|==] N` или `slide_count N-M`): "
            f"{(check.text or '').strip()!r}",
            artifacts, photos, user_phrase,
        )
    predicate, cond_desc = cond
    try:
        actual = len(Presentation(pptx).slides)
    except Exception as e:  # noqa: BLE001
        return unavailable(check, f"Не удалось разобрать .pptx: {e}", artifacts, photos, user_phrase)
    if predicate(actual):
        return ok(check, f"Число слайдов удовлетворяет условию: {cond_desc} (фактически {actual})",
                  artifacts, photos, user_phrase)
    return unavailable(
        check,
        f"Число слайдов не удовлетворяет условию: ожидалось {cond_desc}, получено {actual}",
        artifacts, photos, user_phrase,
    )