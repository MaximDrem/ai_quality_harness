"""Генерация ID сущностей (messageID, sessionID, ...).

Повторяет `packages/opencode/src/id/id.ts`: монотонно возрастающие ID вида
``msg_<12 hex><14 base62>``, где первые 12 hex-символов кодируют
``(timestamp_ms * 0x1000 + counter)`` (48 бит, monotonic в рамках процесса),
а хвост — 14 случайных base62-символов. Именно такие messageID клиент desktop
генерирует сам и передаёт в теле prompt_async (`Identifier.ascending("message")`,
packages/app/src/components/prompt-input/submit.ts).
"""
from __future__ import annotations

import os
import threading
import time

PREFIXES = {
    "event": "evt",
    "session": "ses",
    "message": "msg",
    "permission": "per",
    "question": "que",
    "user": "usr",
    "part": "prt",
    "pty": "pty",
    "tool": "tool",
    "workspace": "wrk",
    "entry": "ent",
}

LENGTH = 26

# Поле времени в префиксе — 48 бит, формат совпадает с opencode:
# 0x1000 отсчётов на миллисекунду плюс монотонный counter в младших битах.
_TIME_SHIFT = 12
_TIME_BITS = 48
_TIME_MASK = (1 << _TIME_BITS) - 1
_COUNTER_MASK = (1 << _TIME_SHIFT) - 1
# Сколько младших бит времени реально помещается в префикс.
_LOW_MS_BITS = _TIME_BITS - _TIME_SHIFT
_LOW_MS_MASK = (1 << _LOW_MS_BITS) - 1

# Состояние монотонной генерации (как module-level lastTimestamp/counter в id.ts).
_lock = threading.Lock()
_last_timestamp = 0
_counter = 0

_BASE62_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _random_base62(length: int) -> str:
    # os.urandom вместо crypto.randomBytes.
    raw = os.urandom(length)
    return "".join(_BASE62_CHARS[b % 62] for b in raw)


def _pack(current: int, counter: int, direction: str) -> int:
    """Уложить (ms, counter) в 48 бит, как id.ts.

    `ms * 0x1000` в 48 бит помещается только до ~70 лет эпохи (2.8e14 > 7.3e15
    на 2026 год), поэтому старшие биты времени усекаются — ровно так же ведёт
    себя opencode (`& 0xFFFFFFFFFFFF`). Усечение сохраняет и монотонность, и
    восстановление времени: `timestamp_of` собирает ms обратно из младших 36
    бит, которых хватает на цикл в 2^36 мс (~2.2 года).
    """
    packed = (current << _TIME_SHIFT) | (counter & _COUNTER_MASK)
    packed &= _TIME_MASK
    if direction == "descending":
        packed = ~packed & _TIME_MASK
    return packed


def create(prefix: str, direction: str = "ascending", timestamp_ms: int | None = None) -> str:
    """Собрать ID: ``<prefix>_<12 hex><14 base62>`` (как opencode id.create)."""
    global _last_timestamp, _counter

    current = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    with _lock:
        if current != _last_timestamp:
            _last_timestamp = current
            _counter = 0
        _counter += 1
        counter = _counter

    packed = _pack(current, counter, direction)
    return f"{prefix}_{packed:012x}{_random_base62(LENGTH - 12)}"


def ascending(prefix: str, given: str | None = None) -> str:
    """ID по возрастанию; заданный `given` валидируется и возвращается как есть
    (как opencode id.ascending)."""
    canonical = PREFIXES.get(prefix, prefix)
    if given:
        if not given.startswith(canonical):
            raise ValueError(f"ID {given} does not start with {canonical}")
        return given
    return create(canonical, "ascending")


def descending(prefix: str, given: str | None = None) -> str:
    canonical = PREFIXES.get(prefix, prefix)
    if given:
        if not given.startswith(canonical):
            raise ValueError(f"ID {given} does not start with {canonical}")
        return given
    return create(canonical, "descending")


def timestamp_of(id_: str) -> int:
    """Извлечь timestamp (мс) из ascending-ID (как opencode id.timestamp).

    В префиксе сохранены только младшие 36 бит `ms` (старшие усечены при
    упаковке), поэтому подставляем их под старшие биты текущего времени.
    """
    prefix = id_.split("_", 1)[0]
    try:
        packed = int(id_[len(prefix) + 1 : len(prefix) + 13], 16)
    except ValueError as e:
        raise ValueError(f"ID {id_!r} has no hex timestamp field") from e
    low_ms = (packed & _TIME_MASK) >> _TIME_SHIFT
    return (int(time.time() * 1000) & ~(_LOW_MS_MASK)) | low_ms


__all__ = ["PREFIXES", "ascending", "descending", "create", "timestamp_of"]