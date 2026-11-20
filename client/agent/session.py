"""Высокоуровневый интерфейс «сессии агента».

`AgentSession` объединяет headless-инстанс (прокси + сервер) и `AgentClient`,
давая удобный API для сценария тест-кейсов:

    with AgentSession(work_dir="/path", model="gigachat/GigaChat-3-Ultra") as s:
        res = s.ask("Создай презентацию ...")
        for artifact in res.artifacts:
            print(artifact.path)

Инстанс поднимается при входе в контекст и останавливается при выходе.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from ..core.config import ClientConfig
from ..core.errors import AgentError, SessionError
from ..core.models import AgentEvent, Artifact
from ..server.server import HeadlessInstance
from .client import AgentClient
from .result import AgentResult

# Как часто перепроверять наличие файла-артефакта на диске (сек).
_ARTIFACT_POLL_INTERVAL = 0.5

# Сколько ждать подключения платформенных MCP (corpsearch) перед первым
# промтом: без этого промт собирается до `connected`, как при холодном старте.
_SYSTEM_MCP_TIMEOUT = 45.0

# Статусы, после которых попытка подключения уже не изменится сама.
_MCP_TERMINAL = ("connected", "failed", "needs_auth", "needs_client_registration")


def _ensure_dir(path: Union[str, Path]) -> Path:
    p = Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


class AgentSession:
    def __init__(
        self,
        work_dir: str,
        cfg: Optional[ClientConfig] = None,
        model: Optional[str] = None,
        agent: str = "build",
        base_dir: Optional[Path] = None,
        log_dir: Optional[Path] = None,
        skills_dir: Optional[Path] = None,
        title: Optional[str] = None,
        start: bool = True,
        event_idle_timeout: Optional[float] = None,
        auth_mode: Optional[str] = None,
    ):
        self.work_dir = _ensure_dir(work_dir)
        # auth_mode действует только когда конфиг не передан целиком.
        cfg = cfg or ClientConfig(**({"auth_mode": auth_mode} if auth_mode else {}))
        self.cfg = cfg.resolve()
        self.model = model or self.cfg.model
        self.agent = agent
        self.base_dir = base_dir or self.cfg.opencode_base_dir
        self.skills_dir = skills_dir or self.cfg.skills_dir
        self.log_dir = log_dir or (Path("/tmp") / "gigatool-headless" / self.work_dir.name)
        self.title = title
        self.event_idle_timeout = event_idle_timeout or self.cfg.event_idle_timeout

        self.instance: Optional[HeadlessInstance] = None
        self.client: Optional[AgentClient] = None
        self.session_id: Optional[str] = None
        self._owns_instance = False
        if start:
            self.start()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> "AgentSession":
        if self.instance is not None:
            return self
        self.instance = HeadlessInstance(
            work_dir=str(self.work_dir),
            cfg=self.cfg,
            model=self.model,
            base_dir=self.base_dir,
            log_dir=self.log_dir,
            skills_dir=self.skills_dir,
        )
        self.instance.start()
        self._owns_instance = True
        self.client = AgentClient(
            base_url=self.instance.base_url,
            username=self.instance.username,
            password=self.instance.password,
            work_dir=str(self.work_dir),
            model=self.model,
            agent=self.agent,
            event_idle_timeout=self.event_idle_timeout,
        )
        if self.title:
            self.session_id = self.client.create_session(title=self.title)
        return self

    def stop(self) -> None:
        if self.client:
            self.client.stop_listening()
        if self.instance and self._owns_instance:
            self.instance.stop()
            self.instance = None
            self._owns_instance = False

    def __enter__(self) -> "AgentSession":
        return self if self.instance is not None else self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ---------------------------------------------------------------- API
    @property
    def url(self) -> str:
        if not self.instance:
            raise SessionError("instance not started")
        return self.instance.base_url

    def _client(self) -> AgentClient:
        if not self.client:
            raise SessionError("client not started")
        return self.client

    def create_session(self, title: Optional[str] = None) -> str:
        self.session_id = self._client().create_session(title=title or self.title)
        self.wait_for_system_mcp()
        return self.session_id

    def wait_for_system_mcp(self, timeout: float = _SYSTEM_MCP_TIMEOUT) -> Dict[str, Any]:
        """Дождаться подключения платформенных MCP до первого промта.

        В desktop первый запрос уходит уже после того, как corpsearch получил
        статус `connected`, — только тогда промт собирается с
        `<mcp-routing-guidance>` и `lazy_search_tool` вместо встроенного
        `corp_search`. Здесь ждём тот же терминальный статус; неудача
        (`failed`/`needs_auth`) тоже терминальна, как в desktop.
        """
        names = self.instance.enabled_system_mcp() if self.instance else ()
        if not names:
            return {}
        deadline = time.monotonic() + timeout
        status: Dict[str, Any] = {}
        while True:
            try:
                status = self._client().http.get("/mcp") or {}
            except Exception:  # noqa: BLE001 — статус не критичен для хода
                status = {}
            # `disabled` — это ещё не вердикт: connect стартует лениво и
            # статус меняется после завершения попытки (см. reconcileSystemServers).
            if all((status.get(name) or {}).get("status") in _MCP_TERMINAL for name in names):
                return status
            if time.monotonic() >= deadline:
                return status
            time.sleep(0.25)

    def on_event(self, handler: Callable[[AgentEvent], None]) -> None:
        self._client().on_event(handler)

    def _prompt_kwargs(self, prompt_kwargs: dict) -> dict:
        kwargs = {"session_id": self.session_id or self.create_session(),
                  "model": self.model, "agent": self.agent}
        kwargs.update(prompt_kwargs)
        return kwargs

    def ask(
        self,
        message: str,
        attachments: Optional[List[str]] = None,
        **prompt_kwargs: Any,
    ) -> AgentResult:
        """Отправить сообщение и дождаться полного ответа агента.

        `attachments` — пути к файлам, которые надо приложить к сообщению.
        Клиент сам обрабатывает их как UI desktop (текст Office, CSV для
        таблиц, путь для презентаций, data-URL для остального), см.
        `client.attachments`. Пути, заданные относительно, считаются от
        `work_dir` сессии.
        """
        if attachments:
            prompt_kwargs["attachments"] = attachments
        return self._client().send_prompt(message, **self._prompt_kwargs(prompt_kwargs))

    def actions(
        self,
        message: str,
        attachments: Optional[List[str]] = None,
        **prompt_kwargs: Any,
    ):
        """Итерироваться по действиям агента (tool/skill/reasoning/text/status)."""
        if attachments:
            prompt_kwargs["attachments"] = attachments
        return self._client().agent_actions(message, **self._prompt_kwargs(prompt_kwargs))

    def artifacts(self, result: AgentResult) -> List[Artifact]:
        return result.artifacts

    # ------------------------------------------------------------ helpers
    def wait_for_artifact(self, result: AgentResult, timeout: float = 300.0) -> Artifact:
        """Дождаться появления артефакта-файла на диске и вернуть его.

        Ждёт, пока путь из `result.artifacts` реально появится в файловой
        системе (агент мог записать файл в work_dir). Относительные пути
        считаются от `work_dir`.
        """
        candidates = list(result.artifacts)
        deadline = timeout + time.time()
        while candidates and time.time() < deadline:
            for art in list(candidates):
                p = Path(art.path)
                if not p.is_absolute():
                    p = self.work_dir / p
                if p.exists():
                    return Artifact(path=str(p), kind=art.kind, detail=art.detail)
            # Ждём вместе, а не исключаем по одному: артефакты создаются
            # асинхронно, и удалённый из списка кандидат мог появиться позже.
            time.sleep(_ARTIFACT_POLL_INTERVAL)
        if candidates:
            raise AgentError(f"artifact not found on disk yet: {candidates[0].path}")
        raise SessionError("no artifacts produced by agent")


__all__ = ["AgentSession"]