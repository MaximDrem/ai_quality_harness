"""Запуск отдельного headless-инстанса бэкенда (gigatool serve + cookie-прокси).

Инстанс — это:
  1. cookie-прокси к GigaChat gateway (Python stdlib, bind на loopback-порт;
     прикрепляет Sudir-куки и форвардит на SberFriend gateway);
  2. `gigatool serve` из packages/opencode, запущенный с env, повторяющим desktop
     (Basic-auth, GIGACHAT_BASE_URL на прокси, OPENCODE_BASE_DIR и т.д.);
  3. health-check и парсинг реального порта из лога.

В сценарии с несколькими тест-кейсами на каждый кейс создаётся СВОЙ экземпляр
`HeadlessInstance` (свои порты, а при необходимости — свой work_dir/base_dir).
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Union

from ..auth import sudir as _sudir
from ..auth.relogin import SudirRelogin
from ..auth.user_info import UserInfo, UserInfoService
from ..core.config import ClientConfig
from ..core.errors import InstanceError, ServerNotReadyError
from .cert_proxy import CertProxy
from .proxy import GigachatProxy, free_port, normalize_cookies
from .self_protection import self_protection_value
from .system_ca import build_bundle
from .system_mcp import injection_from_config
from .xvfb import XvfbDisplay

_PORT_RE = re.compile(r"server listening on (http://[\d:.]+)")

# Node (llhttp/undici) по умолчанию рвёт ответ, у которого заголовки длиннее
# 16 КиБ: `UND_ERR_HEADERS_OVERFLOW`. SberFriend gateway на каждый ответ
# присылает 5 Set-Cookie PLATFORM_SESSION* по ~4 КБ — блок ~16.5 КБ, и запрос
# падал ещё до чтения тела (US01-01-01: 5 ретраев → 502). Лимит поднят с
# запасом: 1 МиБ больше любого реального блока (браузерный предел — 4 КБ на
# куку). Флаг, а не только `Agent({maxHeaderSize})` в лаунчере: он действует на
# все undici-клиенты процесса, включая собственные Client/Agent sidecar'а.
_SIDECAR_MAX_HEADER_SIZE = 1024 * 1024


def sidecar_command(node_bin: str, launcher: Path) -> list[str]:
    """Команда sidecar'а: Node-обёртка с системным CA и поднятым лимитом.

    `--use-openssl-ca` — паритет с desktop: Electron доверяет корпоративному
    корню из хранилища ОС, а голый Node проверяет только встроенный bundle
    Mozilla → MCP `corpsearch` падает с `unable to verify the first
    certificate`, и в промте вместо `<mcp-routing-guidance>` появляется
    `<mcp-temporary-unavailable>`. Флаг не отключает проверку сертификата —
    наоборот, добавляет системные корни к встроенным.
    """
    return [
        node_bin,
        "--use-openssl-ca",
        f"--max-http-header-size={_SIDECAR_MAX_HEADER_SIZE}",
        str(launcher),
    ]


# Имена env, которые решают, какой набор CA видит node. Попадают в шапку
# server.log: по ней видно, с чем реально стартовал процесс (флаги и внешние
# корни), и не приходится угадывать, был ли применён `--use-openssl-ca`.
_TLS_ENV_NAMES = ("NODE_EXTRA_CA_CERTS", "NODE_OPTIONS")


def sidecar_banner(cmd: list[str], env: dict) -> str:
    """Первая строка server.log: точная команда спавна и TLS-окружение."""
    shown = " ".join(cmd)
    extra = ", ".join(f"{name}={env.get(name) or '-'}" for name in _TLS_ENV_NAMES)
    return f"[client] спавн: {shown}\n[client] tls env: {extra}\n"


class HeadlessInstance:
    """Полностью изолированный headless-инстанс сервера opencode."""

    def __init__(
        self,
        work_dir: str,
        cfg: Optional[ClientConfig] = None,
        model: Optional[str] = None,
        port: Optional[int] = None,
        base_dir: Optional[Path] = None,
        log_dir: Optional[Path] = None,
        skills_dir: Optional[Path] = None,
    ):
        self.work_dir = str(Path(work_dir).expanduser().resolve())
        self.cfg = (cfg or ClientConfig()).resolve()
        self.model = model or self.cfg.model
        self.port = port or free_port()
        # base_dir для хранения данных инстанса. По умолчанию (None) не
        # переопределяем OPENCODE_BASE_DIR — бэкенд берёт фолбэк ~/.wsc,
        # как desktop. Свой base_dir задают для изоляции тест-кейсов.
        self.base_dir = base_dir or self.cfg.opencode_base_dir
        # Каталог скиллов: по умолчанию (None) не переопределяем — бэкенд
        # ищет их в `$OPENCODE_BASE_DIR/config` (~/.wsc/config/skills).
        self.skills_dir = skills_dir or self.cfg.skills_dir
        self.log_dir = log_dir or (Path("/tmp") / "gigatool-headless" / Path(self.work_dir).name)
        self.proxy: Optional[Union[GigachatProxy, CertProxy]] = None
        self.process: Optional[subprocess.Popen] = None
        self.xvfb: Optional[XvfbDisplay] = None
        self.server_log_path: Optional[Path] = None
        # Сводный CA-бандл для sidecar'а (см. `system_ca.py`): корни тестовых
        # стендов есть в NSS/бандле рантайма, но не в /etc/ssl/certs.
        self.ca_bundle: Optional[Path] = None
        # Профиль пользователя, отданный sidecar'у через setInjections
        # (User: … в системном промпте). None — запрос не удался, старт не
        # блокируется (как desktop: userInfoDescription?: string | null).
        self.user_info: Optional[UserInfo] = None

        self.username: str = self.cfg.server_username
        self.password: str = self.cfg.server_password

        # Sudir-сессия ИФТ: профиль, MCP, корп-поиск и (в режиме `ift`) шлюз
        # моделей. Авто re-login при 401 (как desktop: notifyExpired ->
        # повторный логин -> setCookies). Логин/verify всегда на origin
        # SberFriend API (shared_api_origin): куки host-only, чужой stand = 401.
        # В режиме `cert` gigachat_upstream — локальный прокси, поэтому
        # вычислять origin из него нельзя.
        self._sudir_base = self.cfg.shared_api_origin
        self.relogin = SudirRelogin(
            self._sudir_base,
            enabled=getattr(self.cfg, "auto_relogin", True),
            on_login=self._apply_relogin,
        )
        # Прод-сессия ШЛЮЗА МОДЕЛЕЙ — отдельно от IFT-сессии: `prod` ходит
        # моделями на прод-стенд, а профиль/MCP/поиск остаются на ИФТ.
        # Парольной формы в проде нет: сессию даёт окно Electron (как desktop,
        # sudir-auth.ts), поэтому re-login идёт через него.
        self.gateway_relogin: Optional[SudirRelogin] = None
        if self.cfg.auth_mode == "prod":
            self.gateway_relogin = SudirRelogin(
                self.cfg.gateway_origin,
                enabled=getattr(self.cfg, "auto_relogin", True),
                on_login=self._apply_gateway_relogin,
                login_fn=self._electron_login,
            )

    # ------------------------------------------------------------ x-display
    def _display(self) -> Optional[str]:
        """DISPLAY для движка. Если окружение уже даёт конкретный DISPLAY —
        используем его, иначе (если Xvfb включён в конфиге) поднимаем свой."""
        if self.xvfb is not None:
            return self.xvfb.resolve_display()
        return self.cfg.xvfb_display if self.cfg.xvfb_auto else (
            os.environ.get("DISPLAY") or None
        )

    # ------------------------------------------------------------------ env
    def _build_server_env(self, proxy_base: str) -> dict:
        env = dict(os.environ)
        # Не задаём OPENCODE_BASE_DIR, если каталог не указан явно: тогда
        # бэкенд сам выбирает фолбэк ~/.wsc (как `bun run dev:desktop`),
        # и headless не запускает скилы из другого места.
        if self.base_dir is not None:
            env["OPENCODE_BASE_DIR"] = str(self.base_dir)
        # Свой набор скиллов: бэкенд подставляет OPENCODE_CONFIG_DIR вместо
        # `$OPENCODE_BASE_DIR/config` в ConfigPaths.directories, откуда и
        # сканирует `skills/*/SKILL.md`. Тем самым тот же каталог читается как
        # корень конфига: рядом со `skills/` по-прежнему нужны `agent/` и, если
        # используется, `wsc.json` — иначе агенты/конфиг возьмутся только из
        # ~/.wsc/config и .wsc вверх по дереву work_dir.
        if self.skills_dir is not None:
            env["OPENCODE_CONFIG_DIR"] = str(self.skills_dir)
        env["OPENCODE_SERVER_USERNAME"] = self.username
        env["OPENCODE_SERVER_PASSWORD"] = self.password
        # Watchdog простоя стрима в opencode (STREAM_IDLE_TIMEOUT_MS в llm.ts):
        # бэкенд-дефолт 120 с рвёт reasoning-модели (glm-5.2), у которых через
        # «long»-шлюз первое SSE-событие на тяжёлых скилл-запросах приходит
        # позже 120 с («Model stalled»). Прокидываем явно (0 = отключить).
        env["GIGATOOL_STREAM_IDLE_TIMEOUT_MS"] = str(getattr(self.cfg, "stream_idle_timeout_ms", 600000))
        # Весь трафик к LLM — через локальный cookie-прокси (он прикрепляет
        # Sudir-куки и форвардит на SberFriend gateway).
        env["GIGACHAT_BASE_URL"] = proxy_base
        env["OPENCODE_CLIENT"] = "desktop"
        # Бинарь Electron-приложения для render-verify скиллов (presentation):
        # скилл перезапускает его через GIGATOOL_NODE/MULTITOOL_NODE, чтобы
        # собрать `<slug>-verify/`. Прокидываем значение из конфига (env
        # GIGATOOL_NODE либо дефолтный dev-бинар electron). Также указываем
        # entry приложения (GIGATOOL_APP_PATH), который этот бинарь загружает
        # в verify-режиме (`--pptx-verify`).
        if self.cfg.gigatool_node:
            env["GIGATOOL_NODE"] = self.cfg.gigatool_node
            env["MULTITOOL_NODE"] = self.cfg.gigatool_node
        if self.cfg.gigatool_app_path:
            env["GIGATOOL_APP_PATH"] = self.cfg.gigatool_app_path
        # Виртуальный X-дисплей для render-verify скиллов (Electron оффскрин
        # без X-сервера не стартует). Прокидываем в env, чтобы все дочерние
        # bash-процессы (deck.js → render-verify → electron) унаследовали его.
        display = self._display()
        if display:
            env["DISPLAY"] = display
        env["OPENCODE_DISABLE_GLOBAL_CLAUDE_CODE_PROMPT"] = "true"
        env["OPENCODE_DISABLE_CLAUDE_CODE_PROMPT"] = "true"
        env["OPENCODE_EXPERIMENTAL_ICON_DISCOVERY"] = "true"
        env["OPENCODE_EXPERIMENTAL_FILEWATCHER"] = "true"
        if self.cfg.session_cookies:
            # Для скила corp_search (корпоративный поиск) sidecar читает куки
            # из env напрямую. Авторизация к модели идёт через прокси.
            env["GIGACHAT_SESSION_COOKIES"] = json.dumps(self.cfg.session_cookies)
        # Как prepareServerEnv (server.ts): sidecar и его скиллы читают адрес
        # SberFriend API и готовый corp-search endpoint из env. Origin берём из
        # конфига (а не из унаследованного env): в `prod` он обязан быть
        # прод-стендом, иначе поиск/сессия уедут на ИФТ.
        env["SHARED_API_URL"] = self.cfg.shared_api_origin
        env["CORP_SEARCH_URL"] = self.cfg.corp_search_url
        # Корпоративная сборка не тянет @opencode-ai/plugin из реестра.
        env["OPENCODE_DISABLE_SDK_INSTALL"] = "true"
        # Серверный бэкенд: dev-канал включается флагом явно
        # (HTTPAPI_DEFAULT_ON_CHANNELS в flag.ts), прод остаётся на hono —
        # как desktop на dev/prod-сборках.
        if self.cfg.experimental_httpapi:
            env["OPENCODE_EXPERIMENTAL_HTTPAPI"] = "true"
        else:
            env.pop("OPENCODE_EXPERIMENTAL_HTTPAPI", None)
        # Обёртка-лаунчер (аналог virtual:opencode-server в desktop): путь к
        # собранному бандлу и host-инъекции (профиль пользователя) нужны
        # только ей.
        env["WSC_NODE_MODULE"] = str(self.cfg.opencode_dist_entry)
        # Корни доверия для node: в обычном терминале тестовые стенды банка
        # подписаны `SberCA Test Ext G2`, которого нет ни в bundle Mozilla, ни
        # в /etc/ssl/certs, — без этого MCP падает на TLS. Бандл собирается в
        # log_dir из NSS, системного хранилища и уже выставленных env-файлов.
        if self.ca_bundle is not None:
            env["NODE_EXTRA_CA_CERTS"] = str(self.ca_bundle)
        env["OPENCODE_PORT"] = str(self.port)
        env["WSC_USER_LOCALE"] = "ru"
        env["WSC_INJECTIONS"] = json.dumps(self._injections(), ensure_ascii=False)
        # Защита приложения (блок `<self-protection>` в системном промте) и
        # куки для `customFetch` sidecar'а. По этим кукам ходят MCP-серверы с
        # `useUserCreds` (corpsearch) и встроенные тулы (corp_search, PoHelper,
        # /models) — как apiFetch в desktop, который прикрепляет Cookie и
        # User-Agent к каждому запросу вне локального прокси.
        base_override = Path(env["OPENCODE_BASE_DIR"]) if env.get("OPENCODE_BASE_DIR") else None
        env["OPENCODE_SELF_PROTECTION"] = self_protection_value(
            self.cfg.desktop_package(),
            base=base_override,
            # `Global.Path.config` = OPENCODE_CONFIG_DIR ?? base/config.
            config_dir=Path(env["OPENCODE_CONFIG_DIR"]) if env.get("OPENCODE_CONFIG_DIR") else None,
            # userData у desktop различается по каналу (`wsc` / `wsc.dev`).
            channel=self.cfg.opencode_channel,
        )
        env["WSC_SESSION_COOKIES"] = normalize_cookies("", self.cfg.session_cookies)
        # Desktop-сборка всегда выключает реплей `functions_state_id`
        # (.env.development/.env.production: FUNCTIONS_STATE_ID_TRANSLATE_DISABLED
        # = "true", server.ts:449 → env sidecar'а). С включённым реплеем
        # gateway отвечает 422 «Function calls count does not match function
        # results count» уже на втором подряд вызове инструмента: id,
        # выданный на первом вызове, не согласуется с историей из двух вызовов
        # (проверено реплеем: со state id — 422, без них — 200).
        env["FUNCTIONS_STATE_ID_TRANSLATE_DISABLED"] = "true"
        # Тот же набор в файле: launcher перечитывает его на каждом запросе,
        # поэтому re-login по ходу кейса подхватывается без перезапуска.
        env["WSC_SESSION_COOKIES_FILE"] = str(self._cookies_file())
        self._write_cookies_file()
        env["WSC_SHARED_API_ORIGIN"] = self.cfg.shared_api_origin
        return env

    def _cookies_file(self) -> Path:
        return self.log_dir / "session-cookies.txt"

    def _write_cookies_file(self) -> None:
        """Сохранить текущие Sudir-куки для launcher customFetch."""
        try:
            self._cookies_file().parent.mkdir(parents=True, exist_ok=True)
            self._cookies_file().write_text(
                normalize_cookies("", self.cfg.session_cookies), encoding="utf-8"
            )
        except OSError:
            pass

    def _injections(self) -> dict:
        """Host-инъекции для `setInjections` (как spawnLocalServer).

        Пустой `userInfo` не передаём: `setInjections` мерджит поля, и
        `undefined` затёр бы значение (server.ts:217-227). `systemMcp` —
        платформенные MCP-серверы из ресурсов desktop: пока corpsearch
        подключён, opencode строит `<mcp-routing-guidance>`, убирает
        встроенный corp_search из видимых тулов и добавляет lazy-каталог.
        """
        injections: dict = {"userLocale": "ru"}
        if self.user_info is not None and self.user_info.description:
            injections["userInfo"] = self.user_info.description
        system_mcp = injection_from_config(
            self.cfg.repo_root,
            self.cfg.shared_api_origin,
            self.cfg.corp_search_url,
            os.environ.get("GIGATOOL_SYSTEM_MCP"),
        )
        if system_mcp:
            injections["systemMcp"] = system_mcp
        return injections

    def enabled_system_mcp(self) -> tuple:
        """Имена включённых платформенных MCP (их ждём перед первым промтом)."""
        entries = self._injections().get("systemMcp") or {}
        return tuple(name for name, entry in entries.items() if entry.get("enabled"))

    # ------------------------------------------------------- sudir session
    def _apply_relogin(self, header: str) -> None:
        """Свежие IFT-куки после Sudir re-login.

        Обновляет сессию профиля/MCP/поиска (env + файл для `customFetch`).
        Прокси моделей трогаем только в `ift`: там это одна и та же сессия. В
        `prod` прокси ходит прод-куками, и подмена их IFT-сессией сломала бы
        шлюз.
        """
        os.environ["GIGACHAT_SESSION_COOKIES"] = header
        self.cfg.session_cookies = _sudir.header_to_dict(header)
        if self.cfg.auth_mode == "ift" and self.proxy is not None:
            self.proxy.set_cookie_header(header)
        self._write_cookies_file()

    def _apply_gateway_relogin(self, header: str) -> None:
        """Свежие прод-куки шлюза моделей (окно Electron).

        Меняет только gateway-состояние: прокси и `cfg.gateway_cookies`. IFT-
        сессия профиля/MCP остаётся нетронутой.
        """
        self.cfg.gateway_cookies = _sudir.header_to_dict(header)
        if self.proxy is not None:
            self.proxy.set_cookie_header(header)
        self._write_gateway_cookies_file()

    @property
    def session_expired_count(self) -> int:
        counts = [self.relogin.count]
        if self.gateway_relogin is not None:
            counts.append(self.gateway_relogin.count)
        return sum(counts)

    def _relogin(self, reason: str, force: bool = False) -> bool:
        """Повторный Sudir-логин (см. `auth.relogin`)."""
        return self.relogin.login(reason, force=force) is not None

    def _electron_login(self) -> Optional[str]:
        """Куки прод-сессии из окна Electron (порт `sudir-auth.ts`).

        Используется как `login_fn` в `SudirRelogin`: cooldown и проверка
        `/session-check` остаются общими, меняется только способ входа.
        Импорт ленивый: модуль нужен только в `prod`, а `client/__init__`
        импортирует этот файл всегда.
        """
        from ..auth.electron_login import ElectronLogin

        return ElectronLogin(
            self.cfg, log_dir=self.log_dir, origin=self.cfg.gateway_origin
        ).login()

    def _gateway_cookies_file(self) -> Path:
        return self.log_dir / "gateway-cookies.txt"

    def _load_gateway_cookies(self) -> None:
        """Подхватить сохранённую прод-сессию шлюза (если env не задан)."""
        if self.cfg.gateway_cookies:
            return
        try:
            header = self._gateway_cookies_file().read_text(encoding="utf-8").strip()
        except OSError:
            return
        if header:
            self.cfg.gateway_cookies = _sudir.header_to_dict(header)

    def _write_gateway_cookies_file(self) -> None:
        """Сохранить прод-куки шлюза рядом с IFT-куками инстанса.

        Как `session-cookies.txt`: launcher перечитывает IFT-файл, а этот
        нужен только для переиспользования прод-сессии между стартами.
        """
        try:
            header = normalize_cookies("", self.cfg.gateway_cookies)
            self._gateway_cookies_file().parent.mkdir(parents=True, exist_ok=True)
            self._gateway_cookies_file().write_text(header, encoding="utf-8")
        except OSError:
            pass

    def _ensure_prod_session(self) -> None:
        """Прод: получить валидную сессию шлюза моделей до старта прокси.

        Парольного входа в проде нет (IdP отдаёт только клиентский
        сертификат), поэтому сессию добывает окно Electron. Сохранённые куки
        используются, если они живы: окно открывается только при 401.
        """
        self._load_gateway_cookies()
        header = normalize_cookies("", self.cfg.gateway_cookies)
        if header and self.gateway_relogin and self.gateway_relogin.verify(header) == 200:
            return
        if header:
            print(
                "[sudir] прод: сохранённые куки шлюза невалидны — открываю окно входа",
                file=sys.stderr,
            )
        else:
            print("[sudir] прод: кук шлюза нет — открываю окно входа (Electron)", file=sys.stderr)
        if not self.gateway_relogin or not self.gateway_relogin.login("preflight", force=True):
            raise InstanceError(
                "не удалось получить прод-сессию: окно Electron не вернуло валидные куки. "
                f"Лог: {self.log_dir / 'electron-login.log'}"
            )

    def _ensure_fresh_session(self) -> None:
        """Pre-flight перед подъёмом инстанса.

        IFT-сессия (профиль/MCP/поиск) проверяется ВСЕГДА, при любом
        `auth_mode` — это dev-инвариант desktop. В `prod` дополнительно
        проверяется отдельная сессия шлюза моделей.
        """
        header = normalize_cookies("", self.cfg.session_cookies)
        if header:
            self.relogin.ensure_fresh(header)
        elif self.cfg.auth_mode != "ift":
            print(
                "[sudir] IFT-кук нет: профиль, MCP и корп-поиск будут недоступны "
                "(модели при этом работают по своей сессии)",
                file=sys.stderr,
            )
        if self.cfg.auth_mode == "prod":
            self._ensure_prod_session()

    def _on_session_expired(self, status: int, url: str) -> None:
        """Слушатель прокси: upstream вернул 401 — пробуем перелогиниться
        (как desktop: session expired -> re-login).

        В `prod` перелогинивается ПРОД-сессия шлюза (Electron), в `ift` —
        общая Sudir-сессия.
        """
        print(f"[sudir] upstream вернул {status} (сессия истекла): {url}", file=sys.stderr)
        relogin = self.gateway_relogin or self.relogin
        relogin.login("session-expired")

    # ------------------------------------------------------------------ run
    def _start_proxy(self) -> str:
        """Поднять транспорт к моделям по режиму авторизации.

        `ift`/`prod` — cookie-прокси на Python (Sudir-сессия к SberFriend
        gateway). `cert` — mTLS-прокси desktop (`scripts/proxy.ts`): авторизация
        к моделям по сертификату, Sudir нужен только для профиля/поиска.
        """
        if self.cfg.auth_mode == "cert":
            self.proxy = CertProxy(self.cfg).start(log_dir=self.log_dir)
            return self.proxy.base_url or ""
        # Куки прокси — сессия ШЛЮЗА (в `prod` отдельная), не IFT-сессия MCP.
        gateway_cookies = self.cfg.gateway_cookies or self.cfg.session_cookies
        self.proxy = GigachatProxy(
            self.cfg,
            port=free_port(),
            upstream=self.cfg.gigachat_upstream,
            cookie_header=normalize_cookies("", gateway_cookies),
        )
        # 401 от upstream -> авто re-login + подмена куки прокси на лету.
        self.proxy.attach_session_expired_listener(self._on_session_expired)
        self.proxy.start(log_dir=self.log_dir)
        return self.proxy.base_url

    @staticmethod
    def _tail_file(path: Path, n: int = 60) -> str:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return f"<нет файла {path}>"
        return "\n".join(text.splitlines()[-n:])

    def _build_sidecar(self) -> None:
        """Собрать бандл sidecar'а при каждом старте инстанса.

        Desktop делает это в `scripts/predev.ts`
        (`cd ../opencode && bun script/build-node.ts`). Канал вкомпилируется в
        бандл (`OPENCODE_CHANNEL` -> InstallationChannel), поэтому сборка
        зависит от режима: ift/cert -> `dev`, prod -> `prod` (см.
        `ClientConfig.opencode_channel`). На старом `dist` не стартуем: иначе
        канал/бэкенд молча разойдутся с выбранным auth_mode.
        """
        cfg = self.cfg
        build_log = self.log_dir / "build.log"
        env = dict(os.environ)
        env["OPENCODE_CHANNEL"] = cfg.opencode_channel
        if cfg.experimental_httpapi:
            env["OPENCODE_EXPERIMENTAL_HTTPAPI"] = "true"

        print(
            f"[sidecar] сборка (канал {cfg.opencode_channel}): bun script/build-node.ts",
            file=sys.stderr,
        )
        cmd = [cfg.bun, "script/build-node.ts"]
        try:
            with open(build_log, "wb") as out:
                proc = subprocess.run(
                    cmd,
                    cwd=str(cfg.opencode_package()),
                    env=env,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    timeout=cfg.build_timeout,
                )
        except subprocess.TimeoutExpired:
            raise InstanceError(
                f"сборка sidecar'а не уложилась в {cfg.build_timeout:.0f} с\n"
                f"{self._tail_file(build_log)}"
            ) from None
        if proc.returncode != 0:
            raise InstanceError(
                f"сборка sidecar'а упала (код {proc.returncode})\n{self._tail_file(build_log)}"
            )
        if not cfg.opencode_dist_entry.exists():
            raise InstanceError(
                f"сборка не создала бандл: {cfg.opencode_dist_entry}\n{self._tail_file(build_log)}"
            )

    def _start_server_process(self, proxy_base: str) -> None:
        cfg = self.cfg
        self.server_log_path = self.log_dir / "server.log"
        # Не `opencode serve`, а Node-обёртка: она грузит dist/node/node.js и
        # передаёт sidecar'у host-инъекции через setInjections — так же, как
        # desktop грузит бандл через алиас virtual:opencode-server. Node (не
        # bun): бандл target=node и импортирует node:sqlite.
        cmd = sidecar_command(cfg.node_bin, cfg.desktop_launcher)
        env = self._build_server_env(proxy_base)
        with open(self.server_log_path, "wb") as out:
            # Шапка пишется тем же fd до exec'а: node дописывает свой вывод
            # после неё, смещение файла общее. Нужна, чтобы после прогона было
            # видно, с какими флагами и TLS-окружением стартовал sidecar.
            out.write(sidecar_banner(cmd, env).encode())
            out.flush()
            self.process = subprocess.Popen(
                cmd,
                cwd=str(cfg.opencode_package()),
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )

    def start(self, wait_ready: bool = True) -> "HeadlessInstance":
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 0) Корни доверия: desktop проверяет TLS через Chromium/Electron и на
        #    dev-канале игнорирует ошибки сертификата для корпоративных хостов.
        #    Голый Node так не умеет, поэтому отдаём ему те же анкоры бандлом.
        self.ca_bundle = build_bundle(self.log_dir / "system-ca.pem")

        # 0.1) Виртуальный X-дисплей (для render-verify скиллов Electron).
        self.xvfb = XvfbDisplay(self.cfg.xvfb_display, enabled=self.cfg.xvfb_auto)
        self.xvfb.start()

        # 1) Pre-flight Sudir-сессии: свежие куки ДО старта прокси/сервера.
        self._ensure_fresh_session()

        # 2) Бандл sidecar'а: канал зависит от auth_mode, поэтому сборка идёт
        #    до спавна и при каждом старте (как predev в desktop).
        self._build_sidecar()

        # 3) Профиль пользователя: в desktop userService.getInfo() вызывается
        #    после логина и до spawnLocalServer; нужен свежим кукам.
        self.user_info = UserInfoService(self.cfg).get_info()

        # 4) Прокси, 5) сервер.
        proxy_base = self._start_proxy()
        self._start_server_process(proxy_base)

        if wait_ready:
            self._wait(port=self.port)
        return self

    def _wait(self, port: int, timeout: Optional[float] = None) -> None:
        timeout = timeout or self.cfg.server_start_timeout
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process is None:
                raise InstanceError("server process is None")
            ret = self.process.poll()
            if ret is not None:
                raise InstanceError(f"server exited with code {ret}\n{self.tail()}")
            m = _PORT_RE.search(self.tail(120))
            if m:
                self.port = int(m.group(1).split(":")[-1])
                self._health_check()
                return self
            time.sleep(0.5)
        raise ServerNotReadyError(f"server did not become ready\n{self.tail()}")

    # -------------------------------------------------------------- health
    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def health(self, timeout: float = 10.0) -> bool:
        url = f"{self.base_url}/global/health"
        auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            return False

    def _health_check(self) -> None:
        deadline = time.time() + self.cfg.health_timeout
        while time.time() < deadline:
            if self.health():
                return
            time.sleep(0.5)
        raise ServerNotReadyError(f"health check failed\n{self.tail()}")

    # ------------------------------------------------------------------ stop
    def stop(self, timeout: float = 10.0) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        if self.proxy:
            self.proxy.stop()
            self.proxy = None
        if self.xvfb:
            self.xvfb.stop(timeout)
            self.xvfb = None

    def tail(self, n: int = 80) -> str:
        if not self.server_log_path or not self.server_log_path.exists():
            return "<no server log>"
        text = self.server_log_path.read_text(errors="replace")
        return "\n".join(text.splitlines()[-n:])

    def __enter__(self) -> "HeadlessInstance":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def create_instance(
    work_dir: str,
    cfg: Optional[ClientConfig] = None,
    model: Optional[str] = None,
    port: Optional[int] = None,
    base_dir: Optional[Path] = None,
    log_dir: Optional[Path] = None,
    skills_dir: Optional[Path] = None,
) -> HeadlessInstance:
    """Удобная фабрика: создаёт инстанс и сразу запускает его."""
    return HeadlessInstance(
        work_dir,
        cfg=cfg,
        model=model,
        port=port,
        base_dir=base_dir,
        log_dir=log_dir,
        skills_dir=skills_dir,
    ).start()


__all__ = ["HeadlessInstance", "create_instance"]