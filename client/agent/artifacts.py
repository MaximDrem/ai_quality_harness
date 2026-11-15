"""Извлечение артефактов (созданных агентом файлов) из событий.

Два источника путей:
  * завершённые вызовы `write`/`edit` — поле `filePath`/`path` в input;
  * упоминания путей прямо в тексте ответа («готово: /path/to/file»).
"""
from __future__ import annotations

from typing import Dict, Iterator, List, Optional

from ..core.models import AgentEvent, Artifact

# Инструменты, записывающие файл на диск.
WRITE_TOOLS = ("write", "edit")

# Ключи input инструмента, где может лежать путь.
_PATH_KEYS = ("filePath", "path", "filename", "dir", "output")
# Для `write`/`edit` url не бывает путём файла.
_FILE_PATH_KEYS = ("filePath", "path", "filename")

_PATH_PREFIXES = ("/", "./", "~")


def path_keys(input_: Dict) -> Iterator[str]:
    """Все непустые строковые значения путеподобных ключей input."""
    for k in _PATH_KEYS:
        v = input_.get(k)
        if isinstance(v, str) and v.strip():
            yield v.strip()


def first_path(input_: Dict, keys=_FILE_PATH_KEYS) -> Optional[str]:
    """Первое путеподобное значение input по порядку ключей."""
    for k in keys:
        v = input_.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _paths_in_text(text: str) -> Iterator[str]:
    for tok in text.split():
        if not tok.startswith(_PATH_PREFIXES):
            continue
        cleaned = tok.strip(".,:;()")
        if cleaned.startswith(_PATH_PREFIXES) and len(cleaned) > 1:
            yield cleaned


def artifacts_from_event(ev: AgentEvent) -> List[Artifact]:
    """Артефакты-файлы, созданные событиями `write`/`edit` и упомянутые в тексте."""
    res: List[Artifact] = []
    for tc in ev.tool_calls:
        if tc.state == "completed" and tc.tool in WRITE_TOOLS:
            path = first_path(tc.input)
            if path:
                res.append(Artifact(path=path, kind="file"))
    # Ищем пути в text-выводах (например, "готово: /path/to/file").
    for t in ev.text_outputs:
        for cleaned in _paths_in_text(t.text):
            res.append(Artifact(path=cleaned, kind="file", detail="from text"))
    return res


def dedupe(artifacts: List[Artifact]) -> List[Artifact]:
    """Убрать повторяющиеся артефакты, сохраняя порядок первого появления.

    Частичный и финальный ответ несут один и тот же путь, а текст может
    упоминать файл дважды — в отчёте каждый файл должен быть один раз.
    """
    seen = set()
    out: List[Artifact] = []
    for art in artifacts:
        if art.path in seen:
            continue
        seen.add(art.path)
        out.append(art)
    return out


__all__ = [
    "WRITE_TOOLS",
    "path_keys",
    "first_path",
    "artifacts_from_event",
    "dedupe",
]