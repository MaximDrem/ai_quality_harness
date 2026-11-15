// Headless-обёртка над собранным sidecar'ом opencode.
//
// Повторяет то, что делает desktop-приложение (`spawnLocalServer` в
// packages/desktop-electron/src/main/server.ts): грузит бандл
// `dist/node/node.js` (в desktop — алиас `virtual:opencode-server`,
// electron.vite.config.ts:99), отдаёт ему host-инъекции через
// `setInjections` и поднимает HTTP-сервер через `Server.listen`.
//
// Почему отдельный процесс, а не env/конфиг: `setInjections` — это
// in-process API sidecar'а (packages/opencode/src/host-injections.ts), в
// частности `userInfo` попадает в системный промпт только так
// (`SystemPrompt.environment` -> блок `User:` внутри `<env>`). Никакая
// переменная окружения этого не заменяет.
//
// Почему Node, а не Bun: бандл собран `target: "node"` и импортирует
// `node:sqlite` (storage/db.ts), которого в Bun 1.3 нет. Desktop исполняет
// тот же бандл в Node-рантайме Electron.
//
// Почему .mjs: Node 22 не исполняет .ts без флагов, а сборка обёртки — лишний
// шаг. Логика — построчный перенос spawnLocalServer/serve.ts.
//
// Порядок важен: `Flag` sidecar'а снапшотит `process.env` при первом импорте
// модуля, поэтому все переменные выставляет Python-родитель ДО спавна этого
// файла, а `import()` здесь — динамический (после чтения env).
//
// Вход (env):
//   WSC_NODE_MODULE            путь к dist/node/node.js (обязателен)
//   OPENCODE_PORT              порт HTTP-сервера (обязателен)
//   WSC_HOSTNAME               hostname (по умолчанию 127.0.0.1)
//   OPENCODE_SERVER_USERNAME   Basic-auth пользователь (по умолчанию opencode)
//   OPENCODE_SERVER_PASSWORD   Basic-auth пароль
//   WSC_INJECTIONS             JSON-объект инъекций: {userInfo?, userLocale?}
//   WSC_SIDECAR_DEBUG          "1" — писать DEBUG-логи sidecar'а в stderr
//
// Выход (stdout): строка `gigatool server listening on http://host:port` —
// тот же формат, что у `gigatool serve` (src/cli/cmd/serve.ts), поэтому
// Python-парсер порта менять не нужно.

import { pathToFileURL } from "node:url"

function stringify(value) {
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value) ?? String(value)
  } catch {
    return String(value)
  }
}

function readInjections() {
  const raw = (process.env.WSC_INJECTIONS ?? "").trim()
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {}
  } catch (err) {
    console.error(`[launcher] WSC_INJECTIONS: невалидный JSON: ${stringify(err)}`)
    return {}
  }
}

async function main() {
  const modulePath = (process.env.WSC_NODE_MODULE ?? "").trim()
  if (!modulePath) {
    console.error("[launcher] WSC_NODE_MODULE не задан (путь к dist/node/node.js)")
    process.exit(2)
  }

  const port = Number((process.env.OPENCODE_PORT ?? "").trim())
  if (!Number.isInteger(port) || port <= 0) {
    console.error(`[launcher] OPENCODE_PORT: ожидался номер порта, получено ${process.env.OPENCODE_PORT}`)
    process.exit(2)
  }

  const hostname = (process.env.WSC_HOSTNAME ?? "").trim() || "127.0.0.1"
  const username = (process.env.OPENCODE_SERVER_USERNAME ?? "").trim() || "opencode"
  const password = process.env.OPENCODE_SERVER_PASSWORD ?? ""

  // env прочитан — только теперь грузим бандл (Flag снапшотит process.env
  // при первом импорте, порядок как в desktop: prepareServerEnv -> import).
  const mod = await import(pathToFileURL(modulePath).href)

  // Аналог installSidecarLogSink (server.ts): default-лог sidecar'а пишет в
  // stderr, но setSink заодно понижает порог до DEBUG. DEBUG не спамим без
  // явного запроса — в server.log он не нужен.
  const debug = process.env.WSC_SIDECAR_DEBUG === "1"
  mod.Log.setSink((level, message, extra) => {
    if (level === "DEBUG" && !debug) return
    const suffix = extra && Object.keys(extra).length > 0 ? ` ${stringify(extra)}` : ""
    process.stderr.write(`[sidecar:${String(level).toLowerCase()}] ${stringify(message)}${suffix}\n`)
  })

  // setInjections мерджит: отсутствующее поле не затирает текущее значение,
  // поэтому пустой userInfo просто не передаём (как server.ts:217-227).
  const injections = readInjections()
  const userInfo = injections.userInfo
  const userLocale = injections.userLocale
  mod.setInjections({
    ...(typeof userInfo === "string" && userInfo ? { userInfo } : {}),
    userLocale:
      (typeof userLocale === "string" && userLocale) || (process.env.WSC_USER_LOCALE ?? "").trim() || "ru",
  })

  // username/password — как в desktop (server.ts:265-270). Сам sidecar читает
  // их из env OPENCODE_SERVER_USERNAME/PASSWORD (ServerAuth.Config), поля в
  // listen() оставлены для совпадения вызова с desktop.
  const listener = await mod.Server.listen({
    port,
    hostname,
    username,
    password,
    cors: ["oc://renderer"],
  })

  console.log(`gigatool server listening on http://${listener.hostname}:${listener.port}`)

  const shutdown = async () => {
    try {
      await listener.stop(true)
    } finally {
      process.exit(0)
    }
  }
  process.on("SIGTERM", shutdown)
  process.on("SIGINT", shutdown)

  // Аналог `yield* Effect.never` в serve.ts: процесс живёт, пока его не
  // остановит родитель.
  await new Promise(() => {})
}

main().catch((err) => {
  console.error(`[launcher] не удалось запустить sidecar: ${stringify(err)}`)
  process.exit(1)
})