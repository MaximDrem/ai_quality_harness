# client/ — headless-клиент агента GigaCat

Модуль этого репозитория (test_agent), а не отдельный проект. Он делает одну
вещь: **поднимает десктопный AI-бэкенд GigaCat (opencode) без Electron UI и
даёт работать с ним по-человечески из Python** — поднять инстанс, отправить
сообщение, посмотреть, что делает агент, дождаться результата и забрать
созданные файлы-артефакты.

Внутри test_agent его использует пакет `example/`:

- `example/agent/session.py` — на каждый тест-кейс поднимает инстанс через
  `AgentSession`, отправляет `user_phrase` и пишет события агента в `raw.log`;
- `example/agent/cookies.py` — обновляет Sudir-куки через `client.auth.sudir`
  (куки живут ~13 часов);
- `example/evaluate.py` — `--list-models` (список моделей через `AgentSession`).

Режим авторизации харнесса задаётся `example/run_test_agent.py --auth-mode
{ift,prod,cert}` и прокидывается в `AgentSession`; по умолчанию — `ift`
(или `GIGATOOL_AUTH_MODE`).

`example` импортирует `client` лениво, внутри функций: к моменту импорта
переменные окружения из `.env` уже должны быть загружены
(`example.config.load_dotenv()`), а часть переменных client читает при импорте.

Клиенту нужны только пакеты из stdlib, pip-зависимостей у него нет (Python 3.9+).

## Что происходит под капотом

Старт инстанса (`HeadlessInstance.start`) идёт тем же порядком и теми же
шагами, что в desktop:

```
client (Python)
 │
 ├─ 0. Xvfb (опционально)          виртуальный X-дисплей: скиллы (presentation)
 │                                 в verify-режиме перезапускают настоящее
 │                                 Electron-приложение для проверки рендера
 ├─ 1. Sudir pre-flight            session-check кук; при 401 — re-login
 │                                 (в prod — окно Electron по сертификату)
 ├─ 2. сборка бандла               bun script/build-node.ts в packages/opencode
 │      OPENCODE_CHANNEL=<канал>   (как scripts/predev.ts; канал — от auth_mode)
 │      -> dist/node/node.js       сборка идёт при КАЖДОМ старте инстанса
 ├─ 3. профиль пользователя        GET current/person + POST search-engine/find
 │                                 -> строка для `User:` в системном промпте
 ├─ 4. транспорт к моделям         cookie-прокси (ift/prod) либо mTLS-прокси
 │      127.0.0.1:<proxy_port>     `scripts/proxy.ts` (cert)
 ├─ 5. sidecar 127.0.0.1:<port>    node server/desktop_launcher.mjs: грузит
 │                                 dist/node/node.js через import() и отдаёт
 │                                 host-инъекции через setInjections
 └─ 6. health                      GET /global/health
```

Профиль пользователя попадает в sidecar **только** через `setInjections` —
это in-process API (`host-injections.ts`), переменной окружения его не
заменить. В `SystemPrompt.environment` он становится блоком
`  User: <описание>` внутри `<env>`, то есть модель знает, кто с ней говорит
(имя, должность, руководитель, телефон, статус отсутствия). В desktop это
делает `user-service.ts` → `spawnLocalServer({ userInfo })`, здесь —
`server/desktop_launcher.mjs`, получающий строку в `WSC_INJECTIONS`. Если
профиль получить не удалось, старт не блокируется (в desktop
`userInfoDescription?: string | null` по той же причине) — блок `User:` просто
не попадает в промпт.

Лаунчер — не `opencode serve`: именно `import()` бандла + `Server.listen`
дают тот же код-путь, что и в Electron (алиас `virtual:opencode-server`).
Рантайм — Node (бандл собран `target: "node"` и импортирует `node:sqlite`,
которого в Bun 1.3 нет), поэтому нужен `node` ≥ 22 в `GIGATOOL_NODEJS`.

Дальше клиент говорит с сервером по HTTP/SSE: создаёт сессию
(`POST /session`), отправляет промпт (`prompt_async`) и читает события агента
с глобального канала `/global/event` (инстансный `/event` отдаёт только
служебные события). Действия агента нормализуются в `AgentEvent`: вызовы
инструментов/скиллов, reasoning, текст. По завершении (idle) артефакты
извлекаются из завершённых `write`/`edit` и упоминаний путей в тексте.

Авторизация на самом сервере — Basic-auth `opencode:<случайный uuid>`, пароль
генерируется на каждый инстанс и наружу из клиента не отдаётся.

### Почему так:

Бэкенд в headless-режиме должен вести себя как в desktop —
иначе мы тестируем не то приложение. Отсюда «странности» в коде:

| Механизм | desktop-ai-app | client |
|---|---|---|
| Запуск sidecar'а | `import()` бандла `dist/node/node.js` в Node-рантайме Electron (алиас `virtual:opencode-server`) → `Server.listen` | `server/desktop_launcher.mjs` под `node`: тот же `import()` + `Server.listen` |
| Сборка бандла | `scripts/predev.ts`: `cd packages/opencode && bun script/build-node.ts` перед стартом | `_build_sidecar()` при каждом старте инстанса, лог — `build.log` |
| Профиль в промпте | `user-service.ts` → `spawnLocalServer({ userInfo })` → `setInjections` | `auth/user_info.py` → `WSC_INJECTIONS` → `setInjections` в лаунчере |
| Блок `<self-protection>` | `self-protection.ts::selfProtectionValue()` → env `OPENCODE_SELF_PROTECTION` | `server/self_protection.py` (те же пути: appPath, resources, `userData` канала, `~/.wsc`) |
| Платформенные MCP | `system-mcp-config.ts` + `computeSystemMcpInjection` → `setInjections({ systemMcp })` | `server/system_mcp.py` → `WSC_INJECTIONS.systemMcp` |
| Куки и лимит заголовков для `customFetch` | `apiFetch` прикрепляет Cookie/UA/Origin к запросам вне прокси и ходит через `undici.fetch` с `Agent({maxHeaderSize: 128 КиБ})` — gateway отдаёт блок `Set-Cookie` ~16 КБ (дефолт undici 16 КиБ → `UND_ERR_HEADERS_OVERFLOW`) | `server/user_fetch.mjs::makeUserFetch()`: то же (куки из `session-cookies.txt`, `Agent({maxHeaderSize: 1 МиБ})`), плюс `--max-http-header-size` в команде спавна — на весь процесс sidecar'а |
| Доверие корпоративному CA | Electron на dev-канале игнорирует ошибку сертификата для любого хоста (`app.ts`, `certificate-error`), а `apiFetch` ходит в корпоративные через `Agent({connect: {rejectUnauthorized: false}})` (`apiClient.ts:205-228`). Листовой серт тестовых стендов выпущен внутренним `SberCA Test Ext G2`, которого нет ни в bundle Mozilla, ни в `/etc/ssl/certs` | Та же политика в `server/user_fetch.mjs::shouldRejectUnauthorized()`: `*.sberbank`/`*.sbrf`/`*.sber*` и dev-канал — нестрогий агент. Плюс на старте собирается сводный CA-бандл (`server/system_ca.py`: NSS + `/etc/ssl/certs` + `runtime-ca-*.pem` + env) в `NODE_EXTRA_CA_CERTS` — он держит строгий путь (прод-канал, не корпоративные хосты) |
| Реплей `functions_state_id` | `.env.development`/`.env.production`: `FUNCTIONS_STATE_ID_TRANSLATE_DISABLED="true"` → env sidecar'а (`server.ts:449`), id серверной истории вызовов на провод не уходят | тот же флаг в `_build_server_env` (иначе gateway отвечает 422 «Function calls count does not match function results count» на втором подряд вызове инструмента) |
| Готовность MCP к первому промту | пользователь пишет уже после `connected` | `AgentSession.wait_for_system_mcp()` ждёт терминальный статус `/mcp` |
| Подключение к opencode | Basic-auth `opencode:<uuid>`, `?directory=` для GET / `x-opencode-directory` | то же (`http/client.py`) |
| messageID | клиентский ULID `Identifier.ascending("msg")` | `core/ids.ascending("msg")`, передаётся в `prompt_async` |
| Отправка промпта | `POST /session` → `prompt_async` (204), результат — только SSE | то же |
| SSE | вечный цикл `/global/event`, реконнект 250 мс, heartbeat-watchdog 15 с, `Last-Event-ID` | `http/event_stream.py` (те же константы) |
| Прокси к GigaChat | `proxy.ts`: UA `GigaChat-*`, срез `authorization`/`cookie`, стрим; `apiFetch`: Origin/Referer, 401 → session expired (throttle 5 с), Set-Cookie в банку | `server/proxy/` (то же; каталог `/models` — объекты шлюза без изменений), режим `cert` — сам `scripts/proxy.ts` за прозрачным tee для дампа/трейса (`server/proxy/tee.py`) |
| Сбои генерации | сервер сам ретраит провайдера (до 8 попыток, 2с→30с), сессия продолжает генерацию; после исчерпания — `session.error` + idle | `_wait_for_idle` не отваливается на retry, собирает `result.retries`/`error_data`; при зависании — `abort` |
| Sudir-авторизация | куки + verify + re-login при 401 | `auth/sudir.py` + `auth/relogin.py` (см. ниже) |

## Режимы авторизации: `auth_mode`

От режима зависят ДВЕ вещи: транспорт к моделям и канал сборки бандла
(`OPENCODE_CHANNEL`). Канал не косметика: от него зависит серверный бэкенд
(`HTTPAPI_DEFAULT_ON_CHANNELS` в `flag.ts`: на `dev` включается
`effect-httpapi`, на `prod` остаётся `hono`) и имя файла БД.

**Всё остальное не зависит от режима.** Профиль пользователя, платформенные
MCP (`corpsearch`) и корпоративный поиск берутся из dev-конфигурации desktop
и всегда ходят на ИФТ (`SHARED_API_URL`, BASE_PATH `/ext`). Это инвариант
сравнения системных промтов: при смене `auth_mode` промт не меняется.

| `auth_mode` | Канал сборки | Бэкенд | Транспорт к GigaChat | Обязательные env |
|---|---|---|---|---|
| `ift` (по умолчанию) | `dev` | `effect-httpapi` | Python cookie-прокси → ИФТ-шлюз, IFT-куки | `GIGACHAT_SESSION_COOKIES` (или креды для re-login) |
| `prod` | `prod` | `hono` | Python cookie-прокси → прод-шлюз, прод-куки из окна Electron | `GIGATOOL_PROD_SHARED_API_URL_ORIGIN` (+ Electron; куки — по желанию) |
| `cert` | `dev` | `effect-httpapi` | `scripts/proxy.ts` (mTLS) через Python tee (дамп тел/трейс; `limit` моделей — из каталога IFT), куки не нужны для моделей | `GIGACHAT_CERT_PATH` + `GIGACHAT_KEY_PATH` (по умолчанию `packages/desktop-electron/gigakeys/{cert.cert,key.key}`) |

Задаётся `GIGATOOL_AUTH_MODE` (env) или `--auth-mode` (CLI), в скрипте —
`AgentSession(..., auth_mode="prod")` / `ClientConfig(auth_mode="cert")`.

`prod` намеренно **не** падает обратно на `SHARED_API_URL`: в `.env` проекта
там ИФТ, и молча уехать прод-шлюзом на ИФТ-стенд нельзя — куки host-only,
сессия вернёт 401. Без `GIGATOOL_PROD_SHARED_API_URL_ORIGIN` будет
`InstanceError` на старте.

В `cert` режиме Sudir всё равно нужен: mTLS закрывает только доступ к
моделям, а профиль и корпоративный поиск идут через SberFriend API с куками.

**Важно про `prod`:** прод-IdP (`idp02.auth.sigma.sbrf.ru`, realm `sigma`) не
имеет парольной формы. На `GET /` он отдаёт 401 «Kerberos Unsupported» и
альтернативный вход по клиентскому сертификату (`alt.idp02…`, «в поле Тема
указан ваш логин»). Поэтому `IFT_LOGIN_USER`/`IFT_PASSWORD` к прод-стенду не
применимы: клиент повторяет desktop-сценарий и поднимает **настоящий
Electron** (`client/server/electron_login.cjs`), который открывает
`BrowserWindow` на `GATEWAY_ORIGIN`, проходит mTLS сертификатом из хранилища
ОС, снимает куки сессии и проверяет `/session-check`. Окно видно — как в
desktop: в нём можно выбрать сертификат или дожать SSO.

Если прод-куки шлюза уже сохранены (`GIGATOOL_PROD_SESSION_COOKIES` или
`<log_dir>/gateway-cookies.txt`) и живы, окно не открывается
(`_ensure_prod_session` сначала проверяет session-check). При 401 от upstream
re-login тоже идёт через окно Electron, а не по паролю.

Прод-сессия относится ТОЛЬКО к шлюзу моделей. Профиль и MCP в `prod` всё
равно используют IFT-сессию (`GIGACHAT_SESSION_COOKIES` / Sudir-пароль):
если её нет, старт не блокируется, но MCP будет `failed`, а промт получит
`<mcp-temporary-unavailable>`.

Если раньше в `GIGACHAT_SESSION_COOKIES` лежали прод-куки (режим `prod` до
разделения сессий), их нужно перенести в `GIGATOOL_PROD_SESSION_COOKIES`, а в
`GIGACHAT_SESSION_COOKIES` держать ИФТ-сессию (или ничего — её обновит
Sudir-логин по `IFT_LOGIN_*`).

### Прод-вход через Electron

| Что | Как |
|---|---|
| Бинарь | `GIGATOOL_ELECTRON`, иначе `packages/desktop-electron/node_modules/electron/dist/electron`, иначе `electron` из PATH |
| Окно | `BrowserWindow` 480×640, in-memory партиция `sudir-auth-<ts>` (ничего не пишется на диск) |
| Сертификат | из хранилища ОС (NSS), которое Electron читает сам — путь в коде не задаётся; при одном сертификате Electron выбирает сам, при нескольких — диалог выбора (как picker в desktop) |
| Куки | `session.cookies.get({})` после возврата на origin; фильтруются по домену origin |
| Проверка | `GET /session-check` == 200 — в окне и повторно из Python |
| Лог | `<log_dir>/electron-login.log` (выбор сертификата, число кук, статус session-check) |

Требуется графическая сессия (`DISPLAY`) и тот же пользователь, у которого в
NSS лежит клиентский сертификат. Для отладки без окна — `GIGATOOL_ELECTRON_HIDDEN=1`
(SSO должен пройти сам, иначе вход не завершится).

Отдельно: прокси читает тело POST и при `Transfer-Encoding: chunked` — sidecar
(undici) присылает `/chat/completions` чанками. Без этого в upstream уходил
пустой запрос, и прод-шлюз отвечал `403` (у `/models` тела нет, поэтому
каталог моделей при этом работал).

Каталог `GET /models` прокси отдаёт ровно так, как его вернул шлюз (как
desktop): `limit`, `modalities`, `reasoning`, `tool_call`, `temperature`. Из
`limit.output` sidecar берёт `max_tokens`, из `modalities.input` — vision-
capabilities (у моделей без image opencode показывает tool `read_image`). Если
каталог пересобрать «по своим константам», запрос уходит с чужим `max_tokens`,
а часть инструментов молча пропадает. По той же причине прокси не добавляет в
тело `reasoning_effort` — это делает только sidecar (`provider/transform.ts`).

## Авторизация: Sudir-куки и две сессии (IFT и шлюз)

В режимах `ift`/`prod` mTLS-сертификаты `gigakeys` не нужны — бэкенд ходит на
SberFriend gateway и авторизуется **Sudir-сессионными куками** (в `cert` —
наоборот: модели по сертификату, куки только для профиля и поиска). IFT-куки
берутся из env `GIGACHAT_SESSION_COOKIES` (header-строка `name=value; ...` или
JSON-объект) либо из `ClientConfig.session_cookies`.

Два правила, без которых всё разваливается:

1. **Две сессии, два назначения.** IFT-сессия (`shared_api_origin` =
   `SHARED_API_URL`, всегда ИФТ) обслуживает Sudir-логин, session-check,
   профиль, corp-search, MCP и `customFetch` sidecar'а. Сессия шлюза
   (`gigachat_upstream`: `ift`/`cert` → ИФТ-путь, `prod` →
   `GIGATOOL_PROD_SHARED_API_URL_ORIGIN`) — только upstream cookie-прокси.
   Кука `PLATFORM_SESSION` выдаётся host-only: сессия одного стенда на другом
   даёт 401, поэтому смешивать их нельзя. Путь ИФТ-шлюза — внешний
   `/sberfriend-api/api/v1/ext/app/.../gigachat/v1` (на ift-s `/int/app/...`
   даёт 404 datapower); прод-BASE_PATH — `/ext`, для стендов с `/int` —
   `GIGATOOL_PROD_BASE_PATH`.
2. **Куки протухают (~13 ч), поэтому есть авто re-login.** Как в desktop
   (`notifyExpired` → повторный логин): инстанс перед стартом проверяет куки
   через session-check, а при 401 от upstream перелогинивается и на лету
   подменяет куки прокси. IFT-сессия — по кред
   `IFT_LOGIN_USER/IFT_LOGING` + `IFT_PASSWORD`; прод-сессия шлюза — окном
   Electron. Отключается `GIGATOOL_AUTO_RELOGIN=0`.

Типичный симптом обоих нарушений — `Model not found: gigachat/<модель>`:
sidecar не смог получить каталог моделей (`GET /models` → 401/404), молча
свалился в дефолтный каталог (одна GigaChat-3-Ultra) и падает на любой другой
модели. Неудачи `/models` прокси пишет в `raw.log` (api_trace).

## Конфигурация

Всё настраивается параметрами `ClientConfig` или переменными окружения
`GIGATOOL_*` / `OPENCODE_*`. **Пути, специфичные для машины, в коде не
хардкодятся** — они прописаны в `.env` корня репозитория и подхватываются
`load_dotenv()` при запуске: `GIGATOOL_REPO_ROOT` (desktop-ai-app),
`GIGATOOL_BUN`, `GIGATOOL_NODE` (dev-бинарь electron), `GIGATOOL_APP_PATH`.

| Переменная | Значение по умолчанию |
|---|---|
| `GIGATOOL_REPO_ROOT` | корень desktop-ai-app (из `.env`; иначе авто-поиск sibling-каталога) |
| `GIGATOOL_BUN` | `bun` из PATH (физический путь — в `.env`) |
| `GIGATOOL_NODEJS` | `node` из PATH — рантайм sidecar'а (нужен ≥ 22: бандл импортирует `node:sqlite`, в Bun 1.3 его нет) |
| `GIGATOOL_AUTH_MODE` | `ift` (варианты: `ift` / `prod` / `cert`; переключает ТОЛЬКО шлюз моделей и канал сборки) |
| `OPENCODE_BASE_DIR` | (не задаём → `~/.wsc`, как у desktop; можно задать свой на инстанс) |
| `GIGATOOL_SKILLS_DIR` | (не задаём → `$OPENCODE_BASE_DIR/config`, т.е. `~/.wsc/config`; каталог, откуда opencode берёт скиллы — выставляется в `OPENCODE_CONFIG_DIR`) |
| `GIGATOOL_SERVER_USERNAME` | `opencode` (имя Basic-auth) |
| `GIGATOOL_SERVER_PASSWORD` | случайный UUID на инстанс |
| `GIGATOOL_MODEL` | `gigachat/GigaChat-3-Ultra` |
| `GIGATOOL_AGENT` | `build` |
| `GIGACHAT_SESSION_COOKIES` | — (IFT-куки Sudir: профиль, MCP, корп-поиск; header-строка или JSON-объект) |
| `GIGATOOL_PROD_SESSION_COOKIES` | — (прод-куки ШЛЮЗА моделей; header-строка или JSON-объект; при отсутствии — окно Electron и файл `gateway-cookies.txt` в `log_dir`) |
| `GIGATOOL_ELECTRON` | — (бинарь Electron для прод-входа; иначе dev-бинарь `packages/desktop-electron/node_modules/electron`, затем PATH) |
| `GIGATOOL_ELECTRON_TIMEOUT` | `300` (сек, ожидание входа в окне Electron) |
| `GIGATOOL_ELECTRON_HIDDEN` | `0` (не показывать окно входа: SSO должен пройти сам) |
| `SHARED_API_URL` | origin SberFriend API (ИФТ) — профиль/MCP/поиск при ЛЮБОМ режиме; по умолчанию `https://shared-api.ift-s.sberfriend.delta.sbrf.ru` |
| `GIGATOOL_PROD_SHARED_API_URL_ORIGIN` | — (**обязателен** при `auth_mode=prod`: origin прод-стенда ТОЛЬКО для шлюза моделей; `SHARED_API_URL` сюда не подставляется) |
| `GIGATOOL_PROD_BASE_PATH` | `/sberfriend-api/api/v1/ext` (BASE_PATH прод-шлюза; для стендов с `/int` — `/sberfriend-api/api/v1/int`) |
| `GIGACHAT_CERT_PATH` / `GIGACHAT_KEY_PATH` | `packages/desktop-electron/gigakeys/{cert.cert,key.key}` (mTLS-серты режима `cert`) |
| `GIGACHAT_PROXY_BASE_URL` | `https://gigachat-ift.sberdevices.delta.sbrf.ru/v1` (куда ходит mTLS-прокси) |
| `GIGATOOL_CERT_PROXY_PORT` | `0` — занять свободный порт; фактический берётся из stdout прокси |
| `FIXED_CATALOGUE` | `true` (фиксированный каталог `CERT_MODELS` в режиме `cert`) |
| `GIGATOOL_GIGACHAT_UPSTREAM` | полный upstream URL (переопределяет origin+путь целиком) |
| `GIGATOOL_UPSTREAM_TIMEOUT` | `900` (сек, запрос прокси к upstream) |
| `GIGATOOL_INSECURE_TLS` | `1` (не проверять TLS upstream — внутренние стенды с самоподписанными сертами) |
| `GIGATOOL_AUTO_RELOGIN` | `1` (перелогин Sudir при 401; требует кредов ниже) |
| `IFT_LOGIN_USER` / `IFT_LOGING` + `IFT_PASSWORD` | креды Sudir для re-login |
| `GIGATOOL_STREAM_IDLE_TIMEOUT_MS` | `600000` (watchdog простоя стрима в opencode; `0` — отключить) |
| `GIGATOOL_NODE` | — (dev-бинарь electron для render-verify; из `.env`; без него verify скиллов пропускается) |
| `GIGATOOL_APP_PATH` | — (entry verify-режима; из `.env`) |
| `GIGATOOL_BROWSER_UA` | — (User-Agent для запросов профиля/корп-поиска; по умолчанию — как у браузера) |
| `GIGATOOL_XVFB_DISPLAY` / `GIGATOOL_XVFB_AUTO` | `:99` / `1` (поднимать Xvfb, если `DISPLAY` не задан) |
| `GIGATOOL_BUILD_TIMEOUT` | `600` (таймаут сборки бандла `bun script/build-node.ts`, сек) |
| `GIGATOOL_START_TIMEOUT` / `GIGATOOL_HEALTH_TIMEOUT` / `GIGATOOL_IDLE_TIMEOUT` | `300` / `30` / `900` (таймауты, сек) |
| `GIGATOOL_SYSTEM_MCP` | `corpsearch` (какие платформенные MCP из `resources/system-mcp.json` включать; `none` — выключить все) |
| `GIGATOOL_DUMP_REQUESTS` | `1` (писать полные тела запросов в `<run>/requests/<sha1>.json`; `0` — выключить; работает во всех `auth_mode`, в `cert` — через tee) |

Логи инстанса лежат в `log_dir` (по умолчанию
`/tmp/gigatool-headless/<work_dir>`): `build.log` — вывод сборки бандла
(канал и ошибки `bun`), `server.log` — лаунчер и sidecar,
`session-cookies.txt` — Sudir-куки, которые launcher перечитывает на каждом
запросе `customFetch` (обновляются после re-login).

Пример набора ключей судьи/стенда — `params_from_env_file` в корне репозитория.

## Требования к окружению

- `bun` — им собирается бандл sidecar'а при **каждом** старте инстанса (путь —
  в `GIGATOOL_BUN`/`.env`). Инкрементальная сборка ~10-15 с, холодная дольше,
  поэтому `GIGATOOL_BUILD_TIMEOUT` = 600 с. Ошибки сборки попадают в
  `build.log` и поднимаются как `InstanceError` — на старом `dist/` клиент не
  стартует, иначе канал в бандле разойдётся с выбранным `auth_mode`.
- `node` ≥ 22 — рантайм sidecar'а (`GIGATOOL_NODEJS`). Бандл собран
  `target: "node"` и импортирует `node:sqlite`: под Bun 1.3 он не запускается.
- Рабочий бэкенд: установленные зависимости desktop-ai-app (у вас должно
  работать `bun run dev:desktop`).
- Валидные Sudir-куки (см. выше) или креды для авто re-login; для
  `auth_mode=cert` — серты `gigakeys` (mTLS) и рабочая сеть до
  `GIGACHAT_PROXY_BASE_URL`.
- Для `auth_mode=prod` — Electron (dev-бинарь desktop или `GIGATOOL_ELECTRON`),
  графическая сессия (`DISPLAY`) и клиентский сертификат в хранилище ОС
  (NSS) — его Electron находит сам, конкретный сертификат/путь/логин в коде
  не прописаны. Без этого прод-вход не пройдёт: парольной формы на
  прод-IdP нет.
- Для render-verify скиллов — electron-бинарь и Xvfb (иначе `-verify/` просто
  не создаётся, это не ошибка).

## Быстрый старт (CLI)

Отдельно от `example/`, для ручной проверки и отладки:

```bash
cd <корень репозитория>

# поднять инстанс и проверить health
PYTHONPATH=. python3 -m client.cli health --work-dir /tmp/my-case

# разовая задача: создать презентацию и получить путь к файлу
PYTHONPATH=. python3 -m client.cli run \
  --work-dir /tmp/my-case \
  --message "Создай презентацию на 5 слайдов о преимуществах ИИ и сохрани в work_dir" \
  --stream

# список доступных агентов сервера
PYTHONPATH=. python3 -m client.cli agents --work-dir /tmp/my-case

# свой набор скиллов: opencode возьмёт `skills/*/SKILL.md` из этого каталога
PYTHONPATH=. python3 -m client.cli run --work-dir /tmp/my-case \
  --skills-dir /path/to/wsc-config \
  --message "Создай презентацию на 5 слайдов и сохрани в work_dir"

# профиль пользователя (что уйдёт в `User:` системного промпта)
PYTHONPATH=. python3 -m client.cli user-info

# то же на другом стенде: prod / cert (по умолчанию ift)
PYTHONPATH=. python3 -m client.cli health --work-dir /tmp/my-case --auth-mode prod
PYTHONPATH=. python3 -m client.cli run --work-dir /tmp/my-case --auth-mode cert \
  --message "Создай презентацию на 3 слайда и сохрани в work_dir"
```

Подкоманды: `health`, `run`, `agents`, `user-info`. Общие флаги: `--work-dir`
(обяз., кроме `user-info`), `--model`, `--agent`, `--base-dir`, `--skills-dir`,
`--auth-mode {ift,prod,cert}`; для `run` — `--message`, `--stream`,
`--skip-permissions`, `--attach` (см. ниже); для `user-info` — `--json`.

`user-info` не поднимает инстанс: он делает те же два запроса, что
`user-service.ts` (current/person → search-engine/find по руководителю), и
печатает либо готовую строку для промпта, либо `--json` с полями. Это самый
быстрый способ проверить куки и разметку профиля, не дожидаясь сборки бандла:

```bash
$ PYTHONPATH=. python3 -m client.cli user-info
Информация о пользователе: Меня зовут Иванов Иван Иванович; Руководитель: Петров Пётр Петрович; Табельный номер: 12345678; Рабочее место: ...; Почта: ...; Должность: Ведущий инженер-программист; Номер телефона: +7 ...; Часовой пояс: 3; Подразделение: ...; Статус: Работает удалённо.

$ PYTHONPATH=. python3 -m client.cli user-info --json
{
  "description": "Информация о пользователе: ...",
  "family_name": "Иванов",
  "given_name": "Иван",
  ...
}
```

Если профиль получить не удалось (нет кук, 401, недоступен стенд), команда
пишет причину в stderr и возвращает код 1 — на старт инстанса это не влияет.

`--skills-dir` (или `GIGATOOL_SKILLS_DIR`, или `ClientConfig.skills_dir`)
заменяет `OPENCODE_CONFIG_DIR` бэкенда: по умолчанию скиллы читаются из
`$OPENCODE_BASE_DIR/config/skills` (`~/.wsc/config/skills`). Важно: в бэкенде
это не «только папка скиллов», а корень конфига целиком — оттуда же берутся
агенты (`agent/`) и `wsc.json`. Поэтому в каталоге, переданном в
`--skills-dir`, должна быть такая же раскладка (`skills/`, `agent/`, …);
иначе агенты и настройки возьмутся только из `~/.wsc/config`.

## Вложения: путь к файлу вместо base64

UI desktop при отправке сообщения не пересылает файл «как есть»: он готовит
части сообщения (`buildRequestParts`), отправляет их в `prompt_async`, а
сервер (`SessionPrompt.resolvePart`) материализует вложения — копирует файл в
`uploads/<session>/<message>/`, извлекает текст Office и раскладывает части в
том виде, в котором они уходят в модель.

Клиент делает то же самое, но снаружи это просто путь к файлу:

```bash
# одно или несколько вложений
python -m client.cli run --work-dir /tmp/t \
  --message "О чём эта презентация?" \
  --attach /path/to/deck.pptx

python -m client.cli run --work-dir /tmp/t \
  --message "Сравни эти таблицы" \
  --attach ./a.xlsx --attach ./b.xlsx
```

В скрипте — параметр `attachments`:

```python
with AgentSession(work_dir="/tmp/t") as s:
    res = s.ask("Что в файле?", attachments=["/path/to/report.xlsx"])
```

Относительные пути считаются от `work_dir` инстанса.

### Что происходит с файлом

Две половины, обе — переиспользование кода desktop-ai-app (он не меняется,
модули только импортируются):

1. **Подготовка** (`prepare_attachment`) — повторяет клиентскую часть UI
   (`build-request-parts.ts`):

   | Тип файла | Что уходит в промпт |
   |---|---|
   | `.pptx` / `.docx` | data-URL с `ignored: true` (`officeTextMode`): сервер сохраняет файл в `uploads/` и рядом с путём кладёт извлечённый текст документа |
   | `.pptx` крупнее 25 MB | ссылка на файл (`file://…`) — так UI отдаёт колоду, которую нельзя прочитать целиком: модель получает путь без текста |
   | `.xlsx` / `.xls` | CSV-«пилюля» (`data:text/csv;base64,…`) + синтетический блок `[<файл>] ```csv … ``` ` (для CSV длиннее 120 000 символов — пометка «оставлен файлом») |
   | картинка, `.pdf`, текст | data-URL + извлечённый текст / inline для `text/plain` |

2. **Материализация** (`resolve_parts_via_server` → bun-мост `bridge.ts`) —
   часть, собирающая то, что уходит модели: прогоняется через серверный
   `SessionPrompt.resolvePart`, который копирует файл в `uploads/`, извлекает
   текст Office и собирает итоговые части.

Сообщение отправляется в `prompt_async` уже подготовленными частями
(`prepare_attachments` + `build_message_parts`): сервер сам развернёт их так
же, как при отправке из UI. Прогонять части через мост перед отправкой нельзя —
он возвращает уже материализованные части, и сервер развернёт их повторно
(в контексте окажутся два header-блока).

Поэтому в модель приходит ровно то, что прислал бы UI: для pptx —
header-блок `[Attached file: not loaded into context, …]` (колода остаётся на
диске для доступа тулом), для таблицы — CSV-блок с содержимым всех листов,
для картинки — файл-часть `image/png`.

Ограничения размеров — как в UI: 25 MB для обычных файлов, 15 MB для картинок.

**Требования:** `desktop-ai-app` должен быть собран (в нём живут модули и
bun-мост), `bun` — в PATH или в `GIGATOOL_BUN`. xlsx/xls конвертируется тем же
пакетом, что у UI (`@datalens-tech/xlsx`), поэтому `node_modules` берётся из
каталога desktop-ai-app.

## Использование в скрипте

```python
from client.agent import AgentRunner, AgentSession

# Простой случай: контекстный менеджер сам поднимает и гасит инстанс.
with AgentSession(work_dir="/tmp/case-1", model="gigachat/GigaChat-3-Ultra") as s:
    res = s.ask("Создай презентацию на 3 слайда и сохрани в work_dir")
    print("final:", res.final_text)
    for art in res.artifacts:
        print("artifact:", art.path)

# Сценарий «инстанс на тест-кейс»: спавн, затем пошаговое наблюдение действий.
runner = AgentRunner(base_work_dir="/tmp/cases")
s = runner.spawn("case-2")
with s:
    for ev in s.actions("Собери отчёт и сохрани файл"):
        for tc in ev.tool_calls:
            print("TOOL", tc.tool, tc.state, tc.input)
        for rzn in ev.reasonings:
            print("THINK", rzn.text)
        for t in ev.text_outputs:
            print("TEXT", t.text, flush=True)
```

Параллельный прогон нескольких кейсов:

```python
results = runner.run_many(
    [("case-a", "Сделай отчёт X"), ("case-b", "Сделай отчёт Y")],
    max_workers=2,
)
```

Для asyncio оборачивайте блокирующий `session.ask`:
`await asyncio.to_thread(session.ask, msg, ...)`.

В test_agent параллельность ограничена `SEMAPHORE_LIMIT = 1`
(`example/config.py`) — агенты идут последовательно.

## Ключевые модули

Пакет разбит на подпакеты; зависимость идёт строго вниз —
`core` → `http` / `auth` / `server.proxy` → `server` → `agent` → `facade` / `cli`.
Единственные нарушения — ленивые (внутри функций) импорты: `auth/user_info.py`
берёт разбор кук из `server.proxy.cookies`, чтобы не дублировать
`normalize_cookies`, а `server/server.py` грузит `auth.electron_login` только в
`prod` (иначе `client/__init__` тянул бы его всегда).

| Модуль | Назначение |
|---|---|
| `core/config.py` | `ClientConfig` — пути, порты, `auth_mode` и канал сборки, модель, upstream, куки, серты, таймауты |
| `core/errors.py` | иерархия исключений (`ClientError`, `AuthenticationError`, …) |
| `core/models.py` | `AgentEvent`, `Artifact`, `TextOutput`, `Reasoning`, `ToolCall` |
| `core/ids.py` | ULID-генератор messageID (как `Identifier.ascending` в бэкенде) |
| `http/client.py` | HTTP-клиент сервера: Basic-auth, `?directory=`/`x-opencode-directory` |
| `http/event_stream.py` | SSE-стрим `/global/event` с реконнектом и watchdog |
| `server/server.py` | `HeadlessInstance`, `create_instance` — сборка бандла, прокси, лаунчер, health |
| `server/desktop_launcher.mjs` | Node-обёртка sidecar'а: `import()` бандла, `setInjections` (профиль), `Server.listen` — аналог `spawnLocalServer` |
| `server/user_fetch.mjs` | `customFetch` sidecar'а — порт `apiFetch`: куки/UA/Origin вне прокси и `undici.fetch` через `Agent` с поднятым лимитом заголовков |
| `server/electron_login.cjs` | Electron-main прод-входа: `BrowserWindow` на origin, mTLS-сертификат из ОС, `cookies.get({})`, `/session-check` — порт `sudir-auth.ts` |
| `auth/electron_login.py` | `ElectronLogin` — запуск окна входа, чтение кук, фильтр по домену, повторная проверка сессии |
| `server/cert_proxy.py` | `CertProxy` — mTLS-прокси `scripts/proxy.ts` для `auth_mode=cert` |
| `server/xvfb.py` | `XvfbDisplay` — виртуальный X-дисплей для render-verify |
| `server/proxy/proxy.py` | `GigachatProxy` — cookie-прокси к GigaChat gateway (stdlib) |
| `server/proxy/handler.py` | HTTP-обработчик прокси: форвард, стрим (в т.ч. чтение chunked-тела), 401 → session expired |
| `server/proxy/models.py` | отбор каталога моделей из ответа шлюза (объекты отдаются как есть: `limit`, `modalities`, `reasoning`) |
| `server/proxy/cookies.py` | разбор кук из env/JSON в Cookie-заголовок |
| `server/proxy/tracing.py` | api-trace запросов прокси (пишется в `raw.log`) и полные тела запросов (`requests/<sha1>.json`) |
| `server/proxy/tee.py` | прозрачный tee перед `scripts/proxy.ts` в режиме `cert`: дамп тел и api-trace (запрос уходит дальше байт-в-байт), `limit` каталога `/models` — из IFT |
| `server/self_protection.py` | `OPENCODE_SELF_PROTECTION` — блок `<self-protection>` и запрет путей приложения |
| `server/system_mcp.py` | платформенные MCP из `resources/system-mcp.json` → `setInjections({ systemMcp })` |
| `auth/sudir.py` | логин в Sudir, проверка сессии, запись кук в env |
| `auth/relogin.py` | `SudirRelogin` — авто re-login с cooldown (пароль или внешний `login_fn` для `prod`) |
| `auth/user_info.py` | `UserInfoService` — профиль сотрудника (`current/person` + `search-engine/find`) → строка `User:` для системного промпта |
| `agent/client.py` | `AgentClient` — сессии, промпты, разбор SSE |
| `agent/events.py` | `parse_event` — нормализация SSE в `AgentEvent` |
| `agent/artifacts.py` | извлечение и дедуп артефактов |
| `agent/result.py` | `AgentResult`, `build_result` |
| `agent/session.py` | `AgentSession` — высокоуровневый интерфейс (то, что использует example) |
| `agent/runner.py` | `AgentRunner` — фабрика инстансов под тест-кейсы |
| `attachments/bridge.py` | `prepare_attachment`, `resolve_attachments` — вложения: путь к файлу → части сообщения как у UI |
| `attachments/bridge.ts` | bun-мост к `SessionPrompt.resolvePart`: серверная материализация, извлечение Office-текста, части для модели |
| `attachments/spreadsheet.ts` | xlsx/xls → CSV тем же пакетом, что у UI (`@datalens-tech/xlsx`) |
| `facade.py` | единый ре-экспорт публичного API (для простых импортов) |
| `cli.py` | CLI `python3 -m client.cli …` |

Старые плоские модули (`config.py`, `agent_client.py`, `sudir_login.py`, …)
удалены — импортируйте из подпакетов, как в примерах ниже.

### Изменения поведения (исправленные дефекты)

- `core.ids.timestamp_of` снова восстанавливает время из messageID (раньше
  терял старшие биты из-за 48-битного переполнения);
- `build_result` дедуплицирует артефакты по пути — один файл попадает в отчёт
  один раз, даже если он и записан тулом, и упомянут в тексте;
- `AgentSession.wait_for_artifact` дожидается появления файла, не выкидывая
  кандидатов из списка (раньше мог упасть «no artifacts produced by agent»);
- `client.cli run --stream` отправляет один промпт (раньше — два);
- `auth.sudir.verify_session` не следует редиректам: 302 на страницу логина
  больше не считается валидной сессией (раньше session-check «подтверждал»
  любые куки, и авто re-login не срабатывал никогда).

## Формат событий и ошибки генерации

Типовые `type` в `AgentEvent` (см. `core/models.py`):

- `message.part.updated` — части ответа: `tool` (скилл/инструмент),
  `reasoning`, `text`;
- `session.status` — `idle` = завершение; `retry` = сервер ретраит провайдера
  после сбоя генерации (генерация **продолжается**, попытки — в `result.retries`);
- `session.error` — терминальная ошибка (ретраи исчерпаны или ошибка не
  ретраибельна); детали в `result.error` / `result.error_data`;
- `permission.asked` — запрос разрешения (авто-выдаётся при
  `dangerously_skip_permissions=True`);
- `question.asked` — агент задаёт вопрос пользователю (в test_agent такой
  кейс дропается как «Вопрос агента», см. корневой README);
- служебные: `server.connected`, `server.heartbeat` (~10 с),
  `message.updated`, `message.part.delta`.

Ошибки генерации ретраит **сервер**, клиент их не дублирует: при `retry`
продолжает ждать idle (окно ожидания продлевается по активности), при
`session.error` возвращает результат с ошибкой, а если сессия молчит дольше
`GIGATOOL_IDLE_TIMEOUT` — абортит её (`POST /session/:id/abort`, как UI
desktop) и поднимает ошибку. Дополнительно доступны `client.abort(session_id)`,
`client.messages(session_id, limit)`, `client.message(session_id, message_id)`.

Пути артефактов могут быть относительными — решать относительно `work_dir`
(см. `AgentSession.wait_for_artifact`).