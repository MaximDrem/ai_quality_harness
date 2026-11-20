"""Прод-логин в СУДИР через настоящий Electron (порт `sudir-auth.ts`).

Зачем: в прод-контуре IdP не показывает парольную форму — вход идёт по
клиентскому сертификату из хранилища ОС (mTLS/SSO), и получить сессию можно
только браузерным движком с доступом к этому хранилищу. Поэтому здесь не
эмуляция HTTP-потока, а запуск Electron и повтор desktop-сценария
(`packages/desktop-electron/src/main/sudir-auth.ts`):

  1. `BrowserWindow` на `SHARED_API_ORIGIN` в in-memory партиции;
  2. mTLS: `select-client-certificate` — сертификат из хранилища ОС (NSS),
     которое Electron читает сам; путь/сертификат в коде не задаются;
  3. после возврата на origin снимаются ВСЕ куки `session.cookies.get({})`;
  4. куки проверяются `GET /session-check` (200) — в окне и повторно здесь.

Референс — TS-код desktop (переписан на JS без electron-vite и без
loader-оверлея). Импортировать его нельзя: `dist` приложения собирается
electron-vite и тянет весь main-процесс.

Запуск вручную (диагностика):

    python -m client.auth.electron_login --origin https://shared-api-... --print

Модуль ничего не пишет в конфиг сам: возвращает Cookie-заголовок, применяет
его вызывающая сторона (`HeadlessInstance._apply_relogin`).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.config import ClientConfig
from ..core.errors import ClientError
from . import sudir

# Сколько ждём само окно сверх его собственного таймаута (спавн Electron,
# первая отрисовка, выход процесса).
_PROCESS_GRACE_S = 60.0
# Хвост лога Electron в тексте ошибки.
_TAIL_LINES = 40


class ElectronLoginError(ClientError):
    """Electron-окно не вернуло валидную сессию."""


def _origin_host(origin: str) -> str:
    return (urllib.parse.urlsplit(origin).hostname or "").lower()


def cookies_for_origin(cookies: List[Dict[str, Any]], origin: str) -> List[Dict[str, Any]]:
    """Оставить куки, применимые к host'у origin (RFC 6265, домен-суффикс).

    `session.cookies.get({})` отдаёт куки всех доменов, которые успело
    посетить окно (IdP + gateway). На gateway нужны только его собственные:
    чужие куки в заголовке безвредны, но раздувают строку и мешают отладке.
    """
    host = _origin_host(origin)
    if not host:
        return list(cookies)
    out = []
    for c in cookies:
        domain = str(c.get("domain") or "").lstrip(".").lower()
        if not domain or host == domain or host.endswith("." + domain):
            out.append(c)
    return out


def build_cookie_header(cookies: List[Dict[str, Any]], origin: str) -> str:
    """Cookie-заголовок `name=value; ...` из списка кук Electron.

    Если фильтр по домену не оставил ничего (нестандартный домен сессии) —
    отдаём все куки, чтобы не потерять сессию, как `_cookies_from_jar` в
    `auth/sudir.py`.
    """
    selected = cookies_for_origin(cookies, origin) or list(cookies)
    parts = []
    for c in selected:
        name = c.get("name")
        if name:
            parts.append(f"{name}={c.get('value', '')}")
    return "; ".join(parts)


class ElectronLogin:
    """Запуск `server/electron_login.cjs` и разбор его результата."""

    def __init__(
        self,
        cfg: ClientConfig,
        log_dir: Optional[Path] = None,
        hidden: Optional[bool] = None,
        timeout: Optional[float] = None,
        origin: Optional[str] = None,
    ):
        self.cfg = cfg
        # Окно входа открывается на стенде ШЛЮЗА МОДЕЛЕЙ (в `prod` — прод),
        # а не на ИФТ: `shared_api_origin` теперь всегда ИФТ (профиль/MCP), и
        # прод-куки с него не получить.
        self.origin = (origin or cfg.gateway_origin).rstrip("/")
        self.log_dir = Path(log_dir) if log_dir else None
        self.hidden = cfg.electron_login_hidden if hidden is None else hidden
        self.timeout = timeout if timeout is not None else cfg.electron_login_timeout

    # ------------------------------------------------------------- binary
    def electron_bin(self) -> str:
        """Путь к Electron: GIGATOOL_ELECTRON, затем dev-бинарь desktop, PATH."""
        if self.cfg.electron_bin:
            return os.path.expandvars(self.cfg.electron_bin)

        pkg = self.cfg.desktop_package()
        candidates = [
            pkg / "node_modules/electron/dist/electron",
            pkg / "node_modules/.bin/electron",
            self.cfg.repo_root / "node_modules/electron/dist/electron",
            self.cfg.repo_root / "node_modules/.bin/electron",
        ]
        for c in candidates:
            if Path(c).exists():
                return str(c)

        found = shutil.which("electron")
        if found:
            return found

        raise ElectronLoginError(
            "Electron не найден: задай GIGATOOL_ELECTRON или установи "
            f"packages/desktop-electron/node_modules/electron (искал в {pkg})"
        )

    @property
    def entry(self) -> Path:
        return Path(__file__).resolve().parent.parent / "server/electron_login.cjs"

    # --------------------------------------------------------------- run
    def _tail(self, path: Path) -> str:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return f"<нет файла {path}>"
        return "\n".join(text.splitlines()[-_TAIL_LINES:])

    def login(self) -> str:
        """Открыть окно входа, вернуть Cookie-заголовок (кидает при неудаче)."""
        origin = self.origin
        if not origin:
            raise ElectronLoginError("не задан origin шлюза моделей (gateway_origin)")

        electron = self.electron_bin()
        entry = self.entry
        if not entry.exists():
            raise ElectronLoginError(f"нет entry Electron-логина: {entry}")

        log_dir = self.log_dir or (Path(tempfile.gettempdir()) / "gigatool-headless")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "electron-login.log"
        out_dir = Path(tempfile.mkdtemp(prefix="wsc-electron-login-"))
        out_path = out_dir / "result.json"

        env = dict(os.environ)
        env["WSC_ELECTRON_URL"] = origin
        env["WSC_ELECTRON_OUT"] = str(out_path)
        env["WSC_ELECTRON_TIMEOUT_MS"] = str(int(self.timeout * 1000))
        env["WSC_ELECTRON_CHANNEL"] = self.cfg.opencode_channel
        if self.hidden:
            env["WSC_ELECTRON_HIDDEN"] = "1"
        # Electron без X-сервера не стартует; берём тот же DISPLAY, что у
        # инстанса (Xvfb уже поднят к этому моменту).
        display = os.environ.get("DISPLAY") or self.cfg.xvfb_display
        if display:
            env["DISPLAY"] = display

        cmd = [electron, str(entry)]
        if os.environ.get("WSC_ELECTRON_NO_SANDBOX") == "1":
            cmd.insert(1, "--no-sandbox")

        print(
            f"[electron-login] запуск окна входа: {Path(electron).name} (origin {origin})",
            file=sys.stderr,
        )
        with open(log_path, "wb") as log:
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )

        deadline = time.time() + self.timeout + _PROCESS_GRACE_S
        payload: Optional[Dict[str, Any]] = None
        try:
            while time.time() < deadline:
                if out_path.exists():
                    try:
                        payload = json.loads(out_path.read_text())
                    except (OSError, ValueError):
                        payload = None
                    if payload is not None:
                        break
                if proc.poll() is not None and not out_path.exists():
                    raise ElectronLoginError(
                        f"Electron-логин завершился без результата (код {proc.returncode})\n"
                        f"{self._tail(log_path)}"
                    )
                time.sleep(0.3)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

        if payload is None:
            raise ElectronLoginError(
                f"Electron-логин не вернул результат за {self.timeout:.0f} с\n{self._tail(log_path)}"
            )

        cookies = payload.get("cookies") or []
        header = build_cookie_header(cookies, origin)
        status = int(payload.get("sessionCheckStatus") or 0)

        if not cookies or not header:
            raise ElectronLoginError(
                "Electron-логин не собрал куки "
                f"(ok={payload.get('ok')}, error={payload.get('error')}, "
                f"session-check={status})\n{self._tail(log_path)}"
            )
        if status != 200:
            raise ElectronLoginError(
                f"Electron-логин: /session-check -> {status} "
                f"(error={payload.get('error')})\n{self._tail(log_path)}"
            )

        # Повторная проверка вне Electron: окно могло закрыться между
        # cookies.get и session-check, а нам нужна сессия, которой доверяет
        # Python-прокси.
        verify = sudir.verify_session(header, base_url=origin)
        if verify != 200:
            raise ElectronLoginError(
                f"куки из Electron не прошли /session-check -> {verify}\n{self._tail(log_path)}"
            )

        print(
            f"[electron-login] OK: {len(cookies)} кук, /session-check 200",
            file=sys.stderr,
        )
        return header


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Прод-логин в СУДИР через Electron (как desktop)")
    ap.add_argument("--origin", help="origin SberFriend API (default: shared_api_origin из конфига)")
    ap.add_argument("--hidden", action="store_true", help="не показывать окно входа")
    ap.add_argument("--timeout", type=float, default=None, help="таймаут ожидания входа, с")
    ap.add_argument("--print", dest="do_print", action="store_true", help="напечатать Cookie-заголовок")
    args = ap.parse_args(argv)

    # `ClientConfig.__post_init__` для prod требует origin из env — CLI-флаг
    # должен работать без дублирования переменной.
    if args.origin and not os.environ.get("GIGATOOL_PROD_SHARED_API_URL_ORIGIN", "").strip():
        os.environ["GIGATOOL_PROD_SHARED_API_URL_ORIGIN"] = args.origin.rstrip("/")

    cfg = ClientConfig()
    cfg = cfg.resolve()
    login = ElectronLogin(cfg, hidden=args.hidden or None, timeout=args.timeout, origin=args.origin)
    try:
        header = login.login()
    except ElectronLoginError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.do_print:
        print(header)
    else:
        print(f"OK: {len(sudir.header_to_dict(header))} кук", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())