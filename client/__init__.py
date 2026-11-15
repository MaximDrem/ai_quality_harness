"""Headless client for the GigaCat (opencode) desktop backend.

Клиент умеет:
  * поднимать отдельный инстанс headless-бэкенда так же, как это делает desktop:
    собрать бандл sidecar'а, поднять транспорт к моделям (cookie-прокси или
    mTLS-прокси), запустить sidecar Node-обёрткой с `setInjections` (профиль
    пользователя в системном промпте);
  * создавать сессию, отправлять сообщения, подписываться на SSE-события `/global/event`;
  * стримить действия агента (skill/tool вызовы, reasoning, текст);
  * дожидаться завершения сессии и получать пути к созданным артефактам.

Дизайн рассчитан на сценарий, когда внешний скрипт поднимает по одному инстансу
на каждый тест-кейс (с разным work_dir) и общается с ним через интерфейс
`AgentSession`.

Структура пакета:

    client/
    ├── core/      ошибки, конфигурация, генерация ID, модели данных
    ├── http/      HTTP-клиент сервера и SSE-стрим /global/event
    ├── server/    headless-инстанс: сборка бандла, лаунчер sidecar'а,
    │              прокси к моделям (cookie/mTLS), Xvfb, Electron-логин
    ├── auth/      Sudir-логин, проверка сессии, авто re-login, профиль
    ├── agent/     клиент агента, сессии, поток действий, артефакты
    ├── facade.py  единый ре-экспорт публичного API
    └── cli.py     `python -m client.cli …`
"""

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
from .core.config import ClientConfig
from .agent.client import AgentClient
from .agent.result import AgentResult
from .agent.runner import AgentRunner
from .agent.session import AgentSession
from .auth.user_info import UserInfo, UserInfoService, get_user_info
from .server.server import HeadlessInstance, create_instance

__all__ = [
    "AgentClient",
    "AgentSession",
    "AgentRunner",
    "AgentResult",
    "ClientConfig",
    "ClientError",
    "AgentError",
    "AuthenticationError",
    "InstanceError",
    "SessionError",
    "SessionExpiredError",
    "ServerNotReadyError",
    "HeadlessInstance",
    "create_instance",
    "UserInfo",
    "UserInfoService",
    "get_user_info",
    "AgentEvent",
    "Artifact",
    "Reasoning",
    "TextOutput",
    "ToolCall",
]

__version__ = "0.2.0"