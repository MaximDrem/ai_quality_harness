"""Каталог моделей GigaChat: отбор из ответа шлюза без искажений.

Шлюз (`…/models` на shared-api) отдаёт уже готовый RawModel[] — с `limit`,
`modalities`, `reasoning`, `tool_call`, `temperature`. Desktop передаёт эти
объекты sidecar'у как есть, поэтому прокси обязан делать то же самое: opencode
берёт из каталога `max_tokens` (`limit.output`), политику reasoning и
`capabilities.input.image` — от последнего зависит, показывать ли модели tool
`read_image` (vision-моделям он не нужен и скрывается в session/prompt.ts).
Пересборка объекта «на глаз» (например, `modalities.input = text+image+pdf`
для всех) ломает и бюджет ответа, и набор инструментов относительно desktop.
"""
from __future__ import annotations

import re
from typing import List

# Пропускаем в каталоге моделей (чат vs эмбеддинги/служебные). Важно: НЕ
# фильтруем по слову "reasoning" — GigaChat-3.5-Reasoning является чат-моделью и
# должна проходить в каталог. Если гейтвей начнёт отдавать служебные reasoning
# эндпоинты, фильтровать надо по префиксу/id (напр. "-reasoning-embedding"), а
# не по подстроке "reasoning", иначе легитимная чат-модель молча пропадёт.
MODEL_SKIP_RE = re.compile(r"embeddings|legacy|preview|lightning", re.IGNORECASE)


def select_gateway_models(data: List[object]) -> List[dict]:
    """Отобрать чат-модели из ответа шлюза, сохранив объекты как есть."""
    models: List[dict] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if MODEL_SKIP_RE.search(str(item["id"])):
            continue
        models.append(dict(item))
    return models


__all__ = [
    "MODEL_SKIP_RE",
    "select_gateway_models",
]