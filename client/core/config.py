"""Центральная конфигурация headless-клиента.

Все пути к бэкенду (пакет opencode / desktop-electron), upstream GigaChat,
Sudir-куки и дефолтные порты собраны здесь. Значения берутся из параметров
конструктора, затем из переменных окружения GIGATOOL_*, затем из дефолтов.
Пути, специфичные для конкретной машины (бинарь bun, бинарь electron, entry
приложения, корень desktop-ai-app) в коде не хардкодятся — они прописаны в
`.env` корня репозитория и подхватываются через `example.config.load_dotenv()`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .errors import InstanceError


def _root() -> Path:
    """Корень репозитория desktop-ai-app (с пакетами бэкенда opencode).

    test_agent может лежать как внутри desktop-ai-app (test_agent/), так и
    рядом с ним (sibling). Поэтому корень ищем так:
      1. если задан GIGATOOL_REPO_ROOT — берём его;
      2. иначе ищем вверх от config.py каталог `desktop-ai-app`, у которого
         есть `packages/opencode` (клиент может быть вложен глубже, например
         `test_agent_2/client/core`);
      3. иначе считаем корнем каталог на 2 уровня выше.
    """
    env = os.environ.get("GIGATOOL_REPO_ROOT")
    if env:
        return Path(env).expanduser().resolve()

    root = Path(__file__).resolve().parents[2]
    for candidate in (root, *root.parents):
        if (candidate / "desktop-ai-app" / "packages" / "opencode").exists():
            return candidate / "desktop-ai-app"
        if (candidate / "packages" / "opencode").exists():
            return candidate
    return root


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


_GATEWAY_PATH = (
    # Тот же путь, что GIGACHAT_BASE_URL в desktop-electron/.env.development:
    # внешний шлюз /ext/app/ (на ift-s путь /int/app/ даёт 404 datapower,
    # а на ift с куками ift-s — 401: куки host-only, стенды не взаимозаменяемы).
    "/sberfriend-api/api/v1/ext/app/ai-assistant/fs-orchestrator-gateway/long/gigachat/v1"
)

# Дефолтный origin ИФТ-стенда, если SHARED_API_URL не задан (значение из
# desktop-electron/.env.development.local.example).
_IAM_SHARED_API_ORIGIN = "https://shared-api.ift-s.sberfriend.delta.sbrf.ru"

# Адрес mTLS-прокси GigaChat для режима `cert` (GIGACHAT_PROXY_BASE_URL из
# `run.sh sert`).
_CERT_PROXY_UPSTREAM = "https://gigachat-ift.sberdevices.delta.sbrf.ru/v1"

_AUTH_MODES = ("ift", "prod", "cert")


def _shared_api_origin(auth_mode: str) -> str:
    """Origin SberFriend API для выбранного режима авторизации.

    `prod` читает ТОЛЬКО GIGATOOL_PROD_SHARED_API_URL_ORIGIN: в `.env` проекта
    SHARED_API_URL указывает на ИФТ, и молча уехать с пром-режимом на ИФТ-стенд
    (или наоборот) нельзя — куки host-only, сессия не подойдёт.

    `ift`/`cert` — SHARED_API_URL (как SHARED_API_URL_ORIGIN в desktop), иначе
    дефолт ИФТ-стенда.
    """
    if auth_mode == "prod":
        origin = os.environ.get("GIGATOOL_PROD_SHARED_API_URL_ORIGIN", "").strip()
        if not origin:
            raise InstanceError(
                "auth_mode=prod требует GIGATOOL_PROD_SHARED_API_URL_ORIGIN "
                "(origin прод-стенда SberFriend API)"
            )
        return origin.rstrip("/")
    origin = os.environ.get("SHARED_API_URL", "").strip()
    return (origin or _IAM_SHARED_API_ORIGIN).rstrip("/")


def _gigachat_upstream(origin: str) -> str:
    """Origin+путь gateway (как GIGACHAT_BASE_URL в desktop-electron).

    Один origin для Sudir-логина (sudir_login читает SHARED_API_URL), профиля
    и API-запросов прокси — иначе host-only куки не подходят и upstream
    отвечает 401/404, а sidecar сваливается в дефолтный каталог (только
    GigaChat-3-Ultra) -> «Model not found: gigachat/...»."""
    return origin.rstrip("/") + _GATEWAY_PATH


@dataclass
class ClientConfig:
    """Параметры, описывающие, где живёт бэкенд и как с ним работать."""

    # Корень репозитория desktop-ai-app.
    repo_root: Path = field(default_factory=_root)
    # Путь к бинарю bun: берётся из GIGATOOL_BUN (физический путь машины
    # прописан в .env корня репозитория); без него — просто `bun` из PATH.
    bun: str = field(default_factory=lambda: _env("GIGATOOL_BUN", "bun"))
    # Node-runtime для запуска собранного sidecar'а. Бандл собирается
    # `target: "node"` и импортирует `node:sqlite` (storage/db.ts), которого в
    # Bun 1.3 нет; desktop исполняет тот же бандл в Node-рантайме Electron.
    # Задаётся GIGATOOL_NODEJS (путь или имя бинаря), по умолчанию `node`.
    node_bin: str = field(default_factory=lambda: _env("GIGATOOL_NODEJS", "node"))
    # base dir для всех данных opencode (log/data/cache/state/config).
    # По умолчанию (как у desktop, `bun run dev:desktop`) переменная не
    # выставляется вовсе — бэкенд сам выбирает фолбэк ~/.gigacat. Задаётся
    # только если явно передан env OPENCODE_BASE_DIR или --base-dir.
    opencode_base_dir: Optional[Path] = field(
        default_factory=lambda: (
            Path(v).expanduser()
            if (v := _env("OPENCODE_BASE_DIR", "")).strip()
            else None
        )
    )
    # Каталог, из которого opencode берёт скиллы (набор `skills/*/SKILL.md`).
    # В бэкенде он же становится корнем конфига: `Global.Path.config` и
    # `ConfigPaths.directories` подставляют OPENCODE_CONFIG_DIR, поэтому оттуда
    # читаются и `skills/`, и `agent/`, и `wsc.json`. По умолчанию (None)
    # переменная не выставляется — скиллы берутся из `$OPENCODE_BASE_DIR/config`
    # (обычно `~/.wsc/config`). Задаётся явно, чтобы запускать агента на другом
    # наборе скиллов без копирования и симлинков.
    skills_dir: Optional[Path] = field(
        default_factory=lambda: (
            Path(v).expanduser()
            if (v := _env("GIGATOOL_SKILLS_DIR", "")).strip()
            else None
        )
    )

    # --- Режим авторизации ---
    # `ift`  — Sudir-логин + cookie-прокси к ИФТ-шлюзу (прежнее поведение);
    # `cert` — Sudir-логин (нужен профиль) + mTLS-прокси к GigaChat
    #          (`run.sh sert`: серты из packages/desktop-electron/gigakeys);
    # `prod` — Sudir-логин + cookie-прокси к прод-шлюзу (`run.sh prom`).
    # От режима зависит и канал сборки sidecar'а (см. opencode_channel).
    auth_mode: str = field(default_factory=lambda: _env("GIGATOOL_AUTH_MODE", "ift").strip().lower())
    # Origin SberFriend API. Единая точка правды для профиля (current/person),
    # корпоративного поиска и шлюза GigaChat: куки PLATFORM_SESSION host-only,
    # поэтому Sudir-логин, профиль и API обязаны ходить на ОДИН стенд.
    # Для `prod` задаётся GIGATOOL_PROD_SHARED_API_URL_ORIGIN (см.
    # `_shared_api_origin`), для остальных — SHARED_API_URL.
    shared_api_origin: Optional[str] = None

    # --- Аутентификация сервера (Basic auth) ---
    server_username: str = field(default_factory=lambda: _env("GIGATOOL_SERVER_USERNAME", "opencode"))
    server_password: str = field(default_factory=lambda: _env("GIGATOOL_SERVER_PASSWORD", ""))

    # --- GigaChat upstream ---
    # Новый backend ходит прямо на SberFriend gateway (см.
    # packages/desktop-electron/.env.development.local.example) и авторизуется
    # Sudir-куками.
    # Origin берётся из shared_api_origin (как SHARED_API_URL_ORIGIN в desktop),
    # чтобы Sudir-логин (sudir_login) и API-запросы прокси ходили на ОДИН
    # host: куки PLATFORM_SESSION выдаются host-only, и сессия, полученная
    # для другого stand'а, на API отвечает 401 -> «Model not found».
    # Пустая строка означает «вывести из origin» (см. `__post_init__`).
    gigachat_upstream: str = field(default_factory=lambda: _env("GIGATOOL_GIGACHAT_UPSTREAM", ""))
    # Сессионные куки (Sudir) для авторизации к GigaChat gateway. Берутся из
    # окружения GIGACHAT_SESSION_COOKIES (header-строка `name=value; ...` либо
    # JSON-объект `{"name": "value"}`) или задаются напрямую в параметре.
    session_cookies: dict = field(default_factory=dict)
    # Таймаут на каждый запрос прокси к upstream. Дефолт 300 с, но тяжёлые
    # reasoning-запросы (glm-5.2 сквозь «long»-шлюз) могут не отдавать даже
    # заголовки дольше 300 с (в прогонах наблюдалось ~258 с до первого байта).
    # Держим выше watchdog стрима (GIGATOOL_STREAM_IDLE_TIMEOUT_MS), чтобы
    # таймаут апстрима не обгонял idle-детектор opencode.
    gigachat_upstream_timeout: float = field(
        default_factory=lambda: float(_env("GIGATOOL_UPSTREAM_TIMEOUT", "900"))
    )
    # Отключать ли проверку TLS-сертификата upstream (внутренние/тестовые
    # stand'ы часто с самоподписанными сертами, как в старом mTLS-режиме).
    insecure_tls: bool = field(
        default_factory=lambda: _env("GIGATOOL_INSECURE_TLS", "1").strip().lower() in ("1", "true", "yes")
    )

    # --- Режим `cert`: локальный mTLS-прокси к GigaChat ---
    # Параметры повторяют `run.sh sert` (packages/desktop-electron) и читаются
    # из тех же переменных, что и `scripts/proxy.ts`, чтобы запускать прокси
    # без переименования env.
    # Серты по умолчанию лежат в packages/desktop-electron/gigakeys/
    # (cert.cert/key.key); подставляются в `__post_init__`, т.к. зависят от
    # repo_root.
    cert_path: Optional[Path] = field(
        default_factory=lambda: (
            Path(v).expanduser()
            if (v := _env("GIGACHAT_CERT_PATH", "")).strip()
            else None
        )
    )
    cert_key_path: Optional[Path] = field(
        default_factory=lambda: (
            Path(v).expanduser()
            if (v := _env("GIGACHAT_KEY_PATH", "")).strip()
            else None
        )
    )
    # Адрес GigaChat, к которому идёт mTLS-прокси (GIGACHAT_PROXY_BASE_URL).
    cert_proxy_base_url: str = field(
        default_factory=lambda: _env("GIGACHAT_PROXY_BASE_URL", "").strip() or _CERT_PROXY_UPSTREAM
    )
    # 0 — занять свободный порт (как free_port в run.sh); иначе — пожелание,
    # фактический порт берётся из stdout прокси (`[proxy] listening on …`).
    cert_proxy_port: int = field(default_factory=lambda: int(_env("GIGATOOL_CERT_PROXY_PORT", "0")))
    # Фиксированный каталог моделей CERT_MODELS (FIXED_CATALOGUE в run.sh).
    fixed_catalogue: bool = field(
        default_factory=lambda: _env("FIXED_CATALOGUE", "true").strip().lower() in ("1", "true", "yes")
    )

    # Автоматический Sudir re-login при 401 от upstream / невалидных куках на
    # старте (как desktop: session expired -> повторный логин). Креды берутся
    # из env IFT_LOGIN_USER/IFT_LOGING + IFT_PASSWORD. Отключается значением 0.
    auto_relogin: bool = field(
        default_factory=lambda: _env("GIGATOOL_AUTO_RELOGIN", "1").strip().lower() in ("1", "true", "yes")
    )

    # Бинарь Electron-приложения, который скиллы (presentation) используют для
    # render-verify (`<slug>-verify/`). В desktop-режиме его выставляет обвязка
    # как GIGATOOL_NODE, в headless — берём из env GIGATOOL_NODE (физический
    # путь dev-бинаря electron прописан в .env корня репозитория). Если не
    # задан и не найден — verify скиллов просто пропускается (папка -verify/
    # не создаётся).
    gigatool_node: Optional[str] = field(
        default_factory=lambda: _env("GIGATOOL_NODE", "") or None
    )

    # Entry-файл приложения, который загружает Electron при verify-режиме
    # (`--pptx-verify`). В dev-сборке это скомпилированный main. Нужен, чтобы
    # скиллы могли перезапустить реальное приложение для render-verify.
    # Entry приложения тоже задаётся в .env через GIGATOOL_APP_PATH.
    gigatool_app_path: Optional[str] = field(
        default_factory=lambda: _env("GIGATOOL_APP_PATH", "") or None
    )

    # --- Прод-логин через Electron (auth_mode=prod) ---
    # В проде IdP не показывает парольную форму: вход по клиентскому
    # сертификату из хранилища ОС (mTLS/SSO). Сессию даёт только настоящий
    # браузерный движок, поэтому клиент запускает Electron и повторяет
    # desktop-сценарий (`sudir-auth.ts`). GIGATOOL_ELECTRON — путь к бинарю;
    # пусто — ищем dev-бинарь в packages/desktop-electron/node_modules.
    electron_bin: Optional[str] = field(
        default_factory=lambda: _env("GIGATOOL_ELECTRON", "") or None
    )
    # Таймаут ожидания входа в окне (с). Прод-CA/сертификат могут отвечать
    # не мгновенно; пользователь может выбрать сертификат вручную.
    electron_login_timeout: float = field(
        default_factory=lambda: float(_env("GIGATOOL_ELECTRON_TIMEOUT", "300"))
    )
    # Скрывать окно входа. По умолчанию окно видно — как desktop: только так
    # пользователь может выбрать сертификат или дожать SSO.
    electron_login_hidden: bool = field(
        default_factory=lambda: _env("GIGATOOL_ELECTRON_HIDDEN", "0").strip().lower()
        in ("1", "true", "yes")
    )

    # Виртуальный X-дисплей для render-verify скиллов. Electron-приложение в
    # verify-режиме рендерит оффскрином, но без X-сервера не стартует (ozone).
    # Headless-инстанс сам поднимает Xvfb и прокидывает DISPLAY в env сервера.
    xvfb_display: str = field(default_factory=lambda: _env("GIGATOOL_XVFB_DISPLAY", ":99"))
    # Автоматически запускать Xvfb, если DISPLAY ещё не выставлен.
    xvfb_auto: bool = field(
        default_factory=lambda: _env("GIGATOOL_XVFB_AUTO", "1").strip().lower() in ("1", "true", "yes")
    )

    # Модель по умолчанию (provider/model), которой будет пользоваться агент.
    model: str = field(default_factory=lambda: _env("GIGATOOL_MODEL", "gigachat/GigaChat-3-Ultra"))
    # Имя агента по умолчанию (primary agent).
    agent: str = field(default_factory=lambda: _env("GIGATOOL_AGENT", "build"))

    # Watchdog «простоя стрима» в opencode (STREAM_IDLE_TIMEOUT_MS в
    # packages/opencode/src/session/llm.ts): если от стрима модели нет ни одного
    # события дольше этого времени, ход считается stalled и запрос рвётся
    # («Model stalled — retrying»). Бэкенд-дефолт — 120 с, но reasoning-модели
    # (glm-5.2) через «long»-шлюз отдают ПЕРВОЕ SSE-событие лишь через 150-170 с
    # (очередь/старт генерации) — при 120 с такой запрос обрывается всегда.
    # 0 отключает watchdog совсем.
    stream_idle_timeout_ms: int = field(
        default_factory=lambda: int(_env("GIGATOOL_STREAM_IDLE_TIMEOUT_MS", "600000"))
    )

    # Таймауты.
    # Сборка sidecar'а (`bun script/build-node.ts`) выполняется при каждом
    # старте инстанса; холодная сборка дольше инкрементальной, поэтому
    # таймауты подняты (значения переопределяются env).
    build_timeout: float = field(default_factory=lambda: float(_env("GIGATOOL_BUILD_TIMEOUT", "600")))
    server_start_timeout: float = field(default_factory=lambda: float(_env("GIGATOOL_START_TIMEOUT", "300")))
    health_timeout: float = field(default_factory=lambda: float(_env("GIGATOOL_HEALTH_TIMEOUT", "30")))
    event_idle_timeout: float = field(default_factory=lambda: float(_env("GIGATOOL_IDLE_TIMEOUT", "900")))

    # Прокидывать ли серверные флаги (аналоги desktop).
    experimental_flags: bool = field(default=True)

    def __post_init__(self) -> None:
        """Нормализовать режим и вывести зависимые адреса/пути.

        Dataclass-поля с default_factory не видят другие поля, поэтому всё,
        что зависит от `auth_mode`/`repo_root`, досчитывается здесь: и при
        `ClientConfig()` напрямую, и при `resolve()`.
        """
        mode = (self.auth_mode or "ift").strip().lower()
        if mode not in _AUTH_MODES:
            raise InstanceError(
                f"неизвестный auth_mode: {self.auth_mode!r} (ожидается {' | '.join(_AUTH_MODES)})"
            )
        self.auth_mode = mode

        if not self.shared_api_origin:
            self.shared_api_origin = _shared_api_origin(mode)
        self.shared_api_origin = self.shared_api_origin.rstrip("/")

        if not self.gigachat_upstream:
            self.gigachat_upstream = _gigachat_upstream(self.shared_api_origin)

        # В режиме `cert` серты по умолчанию — те же, что у `run.sh sert`.
        if self.cert_path is None:
            self.cert_path = self.desktop_package() / "gigakeys/cert.cert"
        if self.cert_key_path is None:
            self.cert_key_path = self.desktop_package() / "gigakeys/key.key"

    def resolve(self) -> "ClientConfig":
        """Подставить переменные окружения в пути и настроить дефолты."""
        session_cookies = self.session_cookies
        # Новый способ авторизации — Sudir-куки из окружения. Если параметр не
        # задан, читаем GIGACHAT_SESSION_COOKIES (header-строка или JSON-объект).
        if not session_cookies:
            env_cookies = os.environ.get("GIGACHAT_SESSION_COOKIES", "").strip()
            if env_cookies:
                import json as _json

                if env_cookies.startswith("{"):
                    try:
                        parsed = _json.loads(env_cookies)
                        if isinstance(parsed, dict):
                            session_cookies = parsed
                    except ValueError:
                        pass
                else:
                    # header-строка `name=value; ...` -> dict
                    session_cookies = {}
                    for part in env_cookies.split(";"):
                        part = part.strip()
                        if "=" in part:
                            k, _, v = part.partition("=")
                            session_cookies[k.strip()] = v.strip()
        cfg = ClientConfig(
            repo_root=self.repo_root,
            bun=os.path.expandvars(self.bun),
            node_bin=os.path.expandvars(self.node_bin),
            opencode_base_dir=self.opencode_base_dir,
            skills_dir=self.skills_dir,
            auth_mode=self.auth_mode,
            shared_api_origin=self.shared_api_origin,
            server_username=self.server_username,
            server_password=self.server_password,
            gigachat_upstream=self.gigachat_upstream,
            session_cookies=session_cookies,
            gigachat_upstream_timeout=self.gigachat_upstream_timeout,
            insecure_tls=self.insecure_tls,
            cert_path=self.cert_path,
            cert_key_path=self.cert_key_path,
            cert_proxy_base_url=self.cert_proxy_base_url,
            cert_proxy_port=self.cert_proxy_port,
            fixed_catalogue=self.fixed_catalogue,
            auto_relogin=self.auto_relogin,
            gigatool_node=self.gigatool_node,
            gigatool_app_path=self.gigatool_app_path,
            electron_bin=self.electron_bin,
            electron_login_timeout=self.electron_login_timeout,
            electron_login_hidden=self.electron_login_hidden,
            xvfb_display=self.xvfb_display,
            xvfb_auto=self.xvfb_auto,
            model=self.model,
            agent=self.agent,
            stream_idle_timeout_ms=self.stream_idle_timeout_ms,
            build_timeout=self.build_timeout,
            server_start_timeout=self.server_start_timeout,
            health_timeout=self.health_timeout,
            event_idle_timeout=self.event_idle_timeout,
            experimental_flags=self.experimental_flags,
        )
        if not cfg.server_password:
            import uuid

            cfg.server_password = uuid.uuid4().hex
        return cfg

    def opencode_package(self) -> Path:
        return self.repo_root / "packages/opencode"

    def desktop_package(self) -> Path:
        return self.repo_root / "packages/desktop-electron"

    @property
    def opencode_entry(self) -> Path:
        return self.opencode_package() / "src/index.ts"

    @property
    def opencode_dist_entry(self) -> Path:
        """Собранный бандл sidecar'а (`virtual:opencode-server` в desktop).

        `script/build-node.ts` кладёт его в `packages/opencode/dist/node/node.js`
        (`dist/` в .gitignore пакета). Именно этот файл лаунчер грузит через
        `import()`, как desktop — через алиас electron-vite.
        """
        return self.opencode_package() / "dist/node/node.js"

    @property
    def desktop_launcher(self) -> Path:
        """Node-обёртка, повторяющая spawnLocalServer из desktop."""
        return Path(__file__).resolve().parent.parent / "server/desktop_launcher.mjs"

    @property
    def electron_login_entry(self) -> Path:
        """Electron-main прод-логина (порт `sudir-auth.ts`)."""
        return Path(__file__).resolve().parent.parent / "server/electron_login.cjs"

    # --- Производные адреса SberFriend API (как utils/env.ts в desktop) ---
    @property
    def current_person_url(self) -> str:
        """GET «кто я» — источник табельного номера (CURRENT_PERSON_URL)."""
        return f"{self.shared_api_origin}/sberfriend-api/api/v1/ext/app/features/current/person"

    @property
    def corp_search_url(self) -> str:
        """POST search-engine/find — обогащение профиля (CORP_SEARCH_URL)."""
        return f"{self.shared_api_origin}/sberfriend-api/api/v1/ext/app/se/search-engine/api/v5/find"

    # --- Канал сборки sidecar'а (InstallationChannel) ---
    @property
    def opencode_channel(self) -> str:
        """OPENCODE_CHANNEL, вкомпилируемый в бандл sidecar'а.

        Как `run.sh prom` (prod) и dev-сборка desktop (dev): от канала зависит
        серверный бэкенд (`HTTPAPI_DEFAULT_ON_CHANNELS` в flag.ts) и имя файла
        БД (storage/db.ts). `cert` — это dev-сборка с mTLS-прокси.
        """
        return "prod" if self.auth_mode == "prod" else "dev"

    @property
    def experimental_httpapi(self) -> bool:
        """Явное включение `effect-httpapi` для dev-канала.

        На dev-канале флаг и так включён по умолчанию
        (`HTTPAPI_DEFAULT_ON_CHANNELS`), но desktop-сборка полагается на этот
        дефолт; выставляем явно, чтобы не зависеть от порядка чтения env.
        """
        return self.opencode_channel != "prod"