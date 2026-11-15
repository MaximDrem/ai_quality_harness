"""Исключения клиента headless-бэкенда GigaCat."""


class ClientError(Exception):
    """Базовая ошибка клиента."""


class AuthenticationError(ClientError):
    """Не удалось пройти аутентификацию на сервере (HTTP 401/403)."""


class ServerNotReadyError(ClientError):
    """Сервер не поднялся в течение таймаута или не ответил на health."""


class InstanceError(ClientError):
    """Ошибка запуска/управления headless-инстансом."""


class SessionError(ClientError):
    """Ошибка управления сессией агента."""


class AgentError(ClientError):
    """Ошибка, поднятая самим агентом (session.error) в ходе выполнения."""


class SessionExpiredError(AuthenticationError):
    """Sudir-сессия истекла (upstream вернул HTTP 401).

    Как в desktop (apiClient.maybeNotifyExpired -> sessionService.notifyExpired):
    интерсептор прокси-слоя замечает 401 и сигнализирует о необходимости
    повторного логина. Генерация при этом падает обычным путём (session.error
    от провайдера), а клиенту нужно получить свежие куки (client/sudir_login.py).
    """