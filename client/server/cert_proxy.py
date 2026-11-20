"""mTLS-прокси к GigaChat для режима `cert` (порт `run.sh sert`).

Desktop в этом режиме поднимает `packages/desktop-electron/scripts/proxy.ts`:
локальный HTTP-прокси, который снимает клиентские `Authorization`/`Cookie`,
прикладывает клиентский сертификат GigaChat и форвардит запросы в апстрим.
Sidecar ходит на `http://127.0.0.1:PORT` (opencode сам добавляет `/models`
и `/chat/completions`; `/v1` уже есть в апстриме прокси).

Здесь повторяется именно лаунчерная часть `run.sh sert`:

  * проверка файлов сертификата/ключа (и предупреждение о правах ключа);
  * `GIGACHAT_CERT_PATH` / `GIGACHAT_KEY_PATH` /
    `GIGACHAT_PROXY_BASE_URL` / `GIGACHAT_PROXY_FIXED_CATALOGUE` в env;
  * порт берётся из фактического stdout прокси
    (`[proxy] listening on http://127.0.0.1:PORT`), а не из free_port: между
    проверкой и bind порт может занять кто-то другой;
  * проверка `GET /v1/models` до старта sidecar — иначе приложение молча уедет
    на baked-in `GIGACHAT_BASE_URL` и покажет одну fallback-модель;
  * гашение сабпроцесса в `stop()`.

Сам `scripts/proxy.ts` — не наш код и не дублируется: он запускается как есть
через bun (как в desktop), со всеми зависимостями пакета desktop-electron.
Sudir-куки этому прокси не нужны: авторизация к моделям идёт по сертификату.

Дамп тел запросов и трейс в raw.log в этом режиме пишет `TeeProxy` — тонкий
Python-хоп между sidecar'ом и bun-прокси (см. `proxy/tee.py`): тело уходит
дальше байт-в-байт, но сохраняется в `<run>/requests/<sha1>.json`.

Там же выравниваются лимиты: `scripts/proxy.ts` отдаёт каталог с фиктивными
`limit` (`context: 131072, output: 8192`), поэтому tee подменяет их значениями
настоящего каталога IFT — `max_tokens` и окно контекста как в `auth_mode=ift`.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional, Union

from ..core.config import ClientConfig
from ..core.errors import InstanceError
from .proxy import GIGACHAT_USER_AGENT, TeeProxy, normalize_cookies

# Строка, которую печатает scripts/proxy.ts сразу после bind (её же читает
# run.sh через grep).
_PORT_RE = re.compile(r"^\[proxy\] listening on http://[\d.]+:(\d+)")

# Порт/модели ждём не дольше этого (run.sh: 30 с).
_READY_TIMEOUT = 30.0


class CertProxy:
    """Менеджер mTLS-прокси GigaChat (сабпроцесс `bun scripts/proxy.ts`)."""

    def __init__(self, cfg: ClientConfig):
        self.cfg = cfg
        self.base_url: Optional[str] = None
        self.port: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None
        self.log_path: Optional[Path] = None
        self.tee: Optional[TeeProxy] = None
        self._logger: Optional[Callable[[Dict], None]] = None
        self._dump_dir: Optional[str] = None
        self._warned = False

    # ------------------------------------------------------------- listeners
    def attach_logger(
        self,
        sink: Callable[[Dict], None],
        dump_dir: Optional[Union[str, "os.PathLike[str]"]] = None,
    ) -> None:
        """Привязать sink трейса и каталог дампа (интерфейс cookie-прокси).

        `sink` — callable(rec: dict) в raw.log кейса; `dump_dir` — каталог
        полных тел запросов (`<run>/requests`). Если прокси ещё не стартовал,
        привязка применяется при `start()`.
        """
        self._logger = sink
        self._dump_dir = str(dump_dir) if dump_dir is not None else None
        if self.tee is not None:
            self.tee.attach_logger(sink, dump_dir=self._dump_dir)

    # --------------------------------------------------------------- helpers
    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def tail(self, n: int = 40) -> str:
        if not self.log_path or not self.log_path.exists():
            return "<no proxy log>"
        text = self.log_path.read_text(errors="replace")
        return "\n".join(text.splitlines()[-n:])

    def _check_files(self) -> None:
        cert = Path(self.cfg.cert_path or "")
        key = Path(self.cfg.cert_key_path or "")
        for path in (cert, key):
            if not path.is_file() or not os.access(path, os.R_OK):
                raise InstanceError(
                    f"auth_mode=cert: нет файла сертификата/ключа {path} "
                    "(задайте GIGACHAT_CERT_PATH / GIGACHAT_KEY_PATH)"
                )
        try:
            perms = key.stat().st_mode & 0o777
        except OSError:
            perms = 0
        if perms and perms not in (0o600, 0o400) and not self._warned:
            self._warned = True
            print(
                f"[cert-proxy] предупреждение: {key} имеет права {perms:o}, ожидались 600",
                file=sys.stderr,
            )

    def _proxy_env(self) -> dict:
        env = dict(os.environ)
        env["GIGACHAT_CERT_PATH"] = str(self.cfg.cert_path)
        env["GIGACHAT_KEY_PATH"] = str(self.cfg.cert_key_path)
        env["GIGACHAT_PROXY_BASE_URL"] = self.cfg.cert_proxy_base_url
        env["GIGACHAT_PROXY_FIXED_CATALOGUE"] = "true" if self.cfg.fixed_catalogue else "false"
        if self.cfg.cert_proxy_port:
            env["GIGACHAT_PROXY_PORT"] = str(self.cfg.cert_proxy_port)
        else:
            env.pop("GIGACHAT_PROXY_PORT", None)
        return env

    def _ift_model_limits(self) -> Optional[Dict[str, dict]]:
        """`{model_id: limit}` из настоящего каталога IFT.

        `scripts/proxy.ts` отдаёт фиктивный каталог: `limit` у всех моделей
        `{context: 131072, output: 8192}`. Из-за этого в `cert` другой
        `max_tokens` (и окно контекста), чем в `ift`, где sidecar получает
        каталог шлюза как есть. Здесь каталог запрашивается тем же способом,
        что и в cookie-прокси: `GET {origin}/models` с Sudir-куками.
        """
        url = self.cfg.gigachat_upstream.rstrip("/") + "/models"
        headers = {"Accept": "application/json", "User-Agent": GIGACHAT_USER_AGENT}
        cookie = normalize_cookies("", self.cfg.session_cookies)
        if cookie:
            headers["Cookie"] = cookie
        ctx = ssl.create_default_context()
        if bool(getattr(self.cfg, "insecure_tls", True)):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30.0, context=ctx) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as err:  # noqa: BLE001
            print(
                f"[cert-proxy] каталог IFT недоступен ({url}): {err}; "
                "лимиты останутся из proxy.ts",
                file=sys.stderr,
            )
            return None
        data = body.get("data") if isinstance(body, dict) else body
        if not isinstance(data, list):
            print(f"[cert-proxy] каталог IFT: неожиданный ответ ({url})", file=sys.stderr)
            return None
        limits = {
            item["id"]: item["limit"]
            for item in data
            if isinstance(item, dict) and item.get("id") and isinstance(item.get("limit"), dict)
        }
        if not limits:
            print(f"[cert-proxy] каталог IFT: в ответе нет limit-ов ({url})", file=sys.stderr)
            return None
        print(f"[cert-proxy] лимиты/контекст моделей: каталог IFT ({len(limits)} моделей)", file=sys.stderr)
        return limits

    def _read_bound_port(self, deadline: float) -> int:
        """Дождаться строки bind в логе прокси и вернуть фактический порт."""
        while time.time() < deadline:
            if not self.running:
                raise InstanceError(f"mTLS-прокси завершился до bind\n{self.tail()}")
            if self.log_path and self.log_path.exists():
                for line in self.log_path.read_text(errors="replace").splitlines():
                    m = _PORT_RE.match(line.strip())
                    if m:
                        return int(m.group(1))
            time.sleep(0.2)
        raise InstanceError(f"mTLS-прокси не стартовал за {_READY_TIMEOUT:.0f} с\n{self.tail()}")

    def _wait_models(self, base_url: str) -> None:
        """GET /v1/models на прокси (как run.sh sert): код 200 обязателен."""
        url = f"{base_url}/models"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                if resp.status == 200:
                    return
                status = resp.status
        except urllib.error.HTTPError as err:
            status = err.code
        except (urllib.error.URLError, OSError) as err:
            raise InstanceError(f"mTLS-прокси не отвечает на {url}: {err}\n{self.tail()}") from None
        raise InstanceError(f"mTLS-прокси вернул HTTP {status} на {url}\n{self.tail()}")

    # ------------------------------------------------------------- lifecycle
    def start(self, log_dir=None, wait_ready: bool = True) -> "CertProxy":
        self._check_files()
        log_dir = Path(log_dir) if log_dir is not None else Path("/tmp") / "gigatool-headless"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / "proxy.log"

        script = self.cfg.desktop_package() / "scripts/proxy.ts"
        if not script.is_file():
            raise InstanceError(f"auth_mode=cert: не найден {script} (проверьте GIGATOOL_REPO_ROOT)")

        requested = self.cfg.cert_proxy_port or 0
        print(
            f"[cert-proxy] bun scripts/proxy.ts (порт {requested or 'свободный'})",
            file=sys.stderr,
        )
        with open(self.log_path, "wb") as out:
            self.process = subprocess.Popen(
                [self.cfg.bun, str(script)],
                cwd=str(self.cfg.desktop_package()),
                env=self._proxy_env(),
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )

        if not wait_ready:
            return self

        deadline = time.time() + _READY_TIMEOUT
        self.port = self._read_bound_port(deadline)
        if requested and self.port != requested:
            print(
                f"[cert-proxy] порт {requested} был занят, прокси встал на {self.port}",
                file=sys.stderr,
            )
        # Прокси монтирует каталог на `/models` и форвардит остальное в
        # апстрим как есть (`{origin}{originalUrl}`), а origin уже содержит
        # `/v1`. Поэтому base — корень прокси без `/v1`: opencode сам
        # добавляет `/models` и `/chat/completions`, как в desktop
        # (`GIGACHAT_BASE_URL="http://localhost:${GIGACHAT_PROXY_PORT}"`).
        # Было: base с `/v1` — давало `{origin}/v1/models` и `/v1/v1/chat/completions`
        # (404 на каталоге и на чате).
        # self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._wait_models(self.base_url)

        # Sidecar ходит не в bun напрямую, а через tee: он сохраняет тела
        # запросов (`<run>/requests/<sha1>.json`) и пишет трейс в raw.log,
        # форвардя сам запрос байт-в-байт (см. proxy/tee.py). Он же подменяет
        # фиктивные limit-ы каталога proxy.ts значениями из IFT.
        self.tee = TeeProxy(
            self.base_url,
            upstream_timeout=float(getattr(self.cfg, "gigachat_upstream_timeout", 300.0)),
            limits_provider=self._ift_model_limits,
        ).start()
        if self._logger is not None:
            self.tee.attach_logger(self._logger, dump_dir=self._dump_dir)
        self.base_url = self.tee.base_url or self.base_url
        return self

    def set_cookie_header(self, header: str) -> None:  # noqa: ARG002
        """No-op: в режиме `cert` прокси не использует Sudir-куки.

        Метод нужен для единого интерфейса с cookie-прокси
        (`HeadlessInstance._apply_relogin`).
        """

    def stop(self, timeout: float = 10.0) -> None:
        if self.tee is not None:
            self.tee.stop()
            self.tee = None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None


__all__ = ["CertProxy"]