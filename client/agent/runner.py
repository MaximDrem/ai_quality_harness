"""Оркестратор для сценария тест-кейсов.

Идея: внешний скрипт, обрабатывающий входные тест-кейсы, на каждый кейс
создаёт ОТДЕЛЬНЫЙ `AgentSession` (свой инстанс и свой work_dir) и выполняет
запросы. Эта фабрика помогает:

  * `spawn(work_dir, ...)`        — создать и запустить отдельную сессию;
  * запускать несколько кейсов параллельно через `concurrent.futures`
    (одному процессу клиента достаточно — порты/инстансы изолированы).

Клиент не зависит от asyncio, но легко вызывается из асинхронного скрипта
через `asyncio.to_thread(...)`.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from ..core.config import ClientConfig
from .session import AgentSession


class AgentRunner:
    """Фабрика `AgentSession` по параметрам тест-кейса."""

    def __init__(
        self,
        cfg: Optional[ClientConfig] = None,
        base_work_dir: Optional[str] = None,
        log_root: Optional[Path] = None,
        default_model: Optional[str] = None,
        default_agent: str = "build",
        skills_dir: Optional[str] = None,
    ):
        self.cfg = (cfg or ClientConfig()).resolve()
        self.base_work_dir = Path(base_work_dir).expanduser() if base_work_dir else None
        if self.base_work_dir:
            self.base_work_dir.mkdir(parents=True, exist_ok=True)
        self.log_root = log_root or (Path("/tmp") / "gigatool-headless")
        self.default_model = default_model or self.cfg.model
        self.default_agent = default_agent
        self.skills_dir = Path(skills_dir).expanduser() if skills_dir else self.cfg.skills_dir

    # ------------------------------------------------------------- spawn
    def spawn(
        self,
        work_dir: str,
        model: Optional[str] = None,
        agent: str = "build",
        title: Optional[str] = None,
        start: bool = True,
        log_dir: Optional[Path] = None,
        skills_dir: Optional[str] = None,
    ) -> AgentSession:
        """Создать агент-сессию под отдельный инстанс в `work_dir`."""
        wd = Path(work_dir).expanduser()
        if self.base_work_dir and not wd.is_absolute():
            wd = self.base_work_dir / wd
        return AgentSession(
            work_dir=str(wd),
            cfg=self.cfg,
            model=model or self.default_model,
            agent=agent or self.default_agent,
            log_dir=log_dir or (self.log_root / wd.name),
            skills_dir=Path(skills_dir).expanduser() if skills_dir else self.skills_dir,
            title=title,
            start=start,
        )

    # --------------------------------------------------------- bulk/async
    def run_many(
        self,
        cases: Iterable[Tuple[str, str]],
        max_workers: int = 4,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Запустить несколько (work_dir, prompt) параллельно.

        Возвращает dict: work_dir -> результат ask (AgentResult или исключение).
        """
        cases_list = list(cases)
        results: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {}
            for wd, prompt in cases_list:
                f = pool.submit(self._run_one, wd, prompt, timeout)
                futs[f] = wd
            for f in futs:
                results[futs[f]] = f.result()
        return results

    def _run_one(self, work_dir: str, prompt: str, timeout: Optional[float] = None) -> Any:
        with self.spawn(work_dir) as s:
            return s.ask(prompt)


# Утилита для оборачивания в asyncio (без импорта asyncio в клиенте).
def to_thread(session: AgentSession, method: str, *args: Any, **kwargs: Any) -> Callable[[], Any]:
    """Пример обёртки для вызова из asyncio: ``await asyncio.to_thread(...)``.

    В Python 3.9+ можно просто использовать `asyncio.to_thread(session.ask, msg)`.
    """

    def _target():
        fn = getattr(session, method)
        return fn(*args, **kwargs)

    return _target


__all__ = ["AgentRunner", "to_thread"]