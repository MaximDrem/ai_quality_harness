"""Единая точка входа в пакет: всё, что раньше лежало в плоских модулях.

Слои пакета (`core` → `http`/`server`/`auth` → `agent`) импортируются
снаружи либо напрямую, либо через этот фасад:

    from client import AgentSession, AgentClient, ClientConfig
    from client import AgentRunner, Artifact, AgentEvent
"""
from __future__ import annotations

from .agent.client import AgentClient, parse_model_ref
from .agent.result import AgentResult, build_result
from .agent.runner import AgentRunner, to_thread
from .agent.session import AgentSession
from .auth import sudir, user_info
from .auth.user_info import UserInfo, UserInfoService, get_user_info
from .core.config import ClientConfig
from .core.errors import (
    AgentError,
    AuthenticationError,
    ClientError,
    InstanceError,
    ServerNotReadyError,
    SessionError,
    SessionExpiredError,
)
from .core.models import AgentEvent, Artifact, Reasoning, TextOutput, ToolCall
from .http.client import HttpClient
from .http.event_stream import EventStream
from .server.cert_proxy import CertProxy
from .server.proxy import GigachatProxy, normalize_cookies
from .server.server import HeadlessInstance, create_instance

__all__ = [
    # конфигурация и ошибки
    "ClientConfig",
    "ClientError",
    "AgentError",
    "AuthenticationError",
    "InstanceError",
    "SessionError",
    "SessionExpiredError",
    "ServerNotReadyError",
    # модели данных
    "AgentEvent",
    "AgentResult",
    "Artifact",
    "Reasoning",
    "TextOutput",
    "ToolCall",
    # агент
    "AgentClient",
    "AgentSession",
    "AgentRunner",
    "build_result",
    "parse_model_ref",
    "to_thread",
    # инстанс и транспорт
    "HeadlessInstance",
    "create_instance",
    "GigachatProxy",
    "CertProxy",
    "normalize_cookies",
    "HttpClient",
    "EventStream",
    # аутентификация и профиль
    "sudir",
    "user_info",
    "UserInfo",
    "UserInfoService",
    "get_user_info",
]