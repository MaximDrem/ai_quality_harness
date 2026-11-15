"""Типы событий агента, возникающих во время выполнения сессии.

События получаются из SSE-потока `/event` (payload — BusEvent). Здесь мы
нормализуем интересующие нас типы (tool/skill/reasoning/text/artifact/status)
в удобные dataclass'ы.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TextOutput:
    """Текстовый вывод ассистента (часть типа ``text``)."""

    text: str


@dataclass
class Reasoning:
    """Размышление агента (часть типа ``reasoning``)."""

    text: str


@dataclass
class ToolCall:
    """Вызов инструмента (часть типа ``tool``).

    toolkit == "skill" означает, что был вызван skill (навык).
    """

    tool: str
    state: str  # input | running | completed | error
    input: Dict[str, Any] = field(default_factory=dict)
    output: Optional[str] = None
    error: Optional[str] = None
    toolkit: Optional[str] = None  # если пришло из part.info

    @property
    def is_skill(self) -> bool:
        return self.toolkit == "skill" or self.tool.startswith("skill:")


@dataclass
class Artifact:
    """Артефакт (например, созданный файл презентации).

    Поле ``path`` — абсолютный или относительный путь к файлу. Он может быть
    извлечён из вывода tool (write/shell) или из контента ответа.
    """

    path: str
    kind: str = "file"  # file | url | ...
    detail: Optional[str] = None


@dataclass
class AgentEvent:
    """Нормализованное событие агента.

    ``type`` — одно из: session.connected, message.updated, message.part.updated,
    session.status, session.error, permission.asked, server.heartbeat, ...
    """

    type: str
    session_id: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    # Удобные хелперы (заполняются парсером для важных типов).
    text_outputs: List[TextOutput] = field(default_factory=list)
    reasonings: List[Reasoning] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)

    @property
    def is_idle(self) -> bool:
        return (
            self.type == "session.status"
            and isinstance(self.properties.get("status"), dict)
            and self.properties["status"].get("type") == "idle"
        )

    @property
    def status(self) -> Optional[Dict[str, Any]]:
        """Поле status события session.status ({type: busy|idle|retry|...})."""
        if self.type == "session.status":
            st = self.properties.get("status")
            return st if isinstance(st, dict) else None
        return None

    @property
    def is_retry(self) -> bool:
        """session.status с type=retry — сервер ретраит провайдера после
        сбоя генерации; генерация ПРОДОЛЖАЕТСЯ (см. desktop session/retry.ts:
        политика ретраев -> session.next.retried + status retry)."""
        return (self.status or {}).get("type") == "retry"

    @property
    def error(self) -> Optional[Dict[str, Any]]:
        """Структурированная ошибка события session.error
        (дискриминант по name, детали в data)."""
        if self.type == "session.error":
            err = self.properties.get("error")
            return err if isinstance(err, dict) else None
        return None