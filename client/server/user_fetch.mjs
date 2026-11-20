// Wrapper над fetch для sidecar'а — порт apiFetch из desktop apiClient.ts.
//
// Нужен там, где sidecar ходит не через наш Python-прокси, а напрямую:
// MCP-серверы с `useUserCreds` (corpsearch), встроенный corp_search, PoHelper,
// каталог моделей и сами запросы к GigaChat (`provider/sdk/gigachat/http.ts`
// зовёт getCustomFetch). Desktop прикрепляет к таким запросам Sudir-куки,
// браузерный User-Agent и Origin/Referer; без этого апстрим отвечает 401.
//
// Лимит заголовков: у undici по умолчанию 16 КиБ, а SberFriend отдаёт 5
// Set-Cookie по ~4 КБ (~16.5 КБ блока) → `UND_ERR_HEADERS_OVERFLOW` ещё до
// чтения тела. Desktop поднимает лимит на своём Agent (apiClient.ts:218-221),
// здесь — то же самое; прокси при этом ничего не режет.
//
// TLS — тоже порт apiClient.ts. Листовой сертификат тестовых стендов выпущен
// внутренним `SberCA Test Ext G2`, которого нет ни в bundle Mozilla, ни в
// хранилище ОС; desktop на dev-канале вообще игнорирует ошибку сертификата для
// корпоративных хостов (`app.ts`, `certificate-error`) и ходит в них через
// `Agent({ connect: { rejectUnauthorized: false } })` (apiClient.ts:205-228).
// Здесь та же политика: `*.sberbank|*.sbrf|*.sber*` и dev-канал — нестрогий
// агент, остальное — строгая проверка.
//
// Запросы на loopback не получают куки (к локальному прокси их прикрепляет он
// сам, дубль `Cookie` недопустим), но идут тем же диспетчером: лимит заголовков
// нужен и на ответах прокси.
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { pathToFileURL } from "node:url"

// Как getUserAgent() в desktop (session.defaultSession.getUserAgent()):
// апстримы (MCP, corp-search) видят браузерный UA, а не node-фетч.
const DEFAULT_USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

// Как MAX_HEADER_SIZE в desktop apiClient.ts:218-221. Значение — потолок для
// одного блока заголовков; 1 МиБ заведомо больше любого реального (браузерный
// предел — 4 КБ на куку), т.е. на практике лимита нет.
export const MAX_HEADER_SIZE = 1024 * 1024

// Как TRUSTED_HOST_RE в apiClient.ts:202 — известные внутренние хосты.
const TRUSTED_HOST_RE = /(\.sberbank|\.sbrf|\.sber\w*)(\.\w+)?$/i

/**
 * Нужна ли строгая проверка цепочки — дословный порт
 * `shouldRejectUnauthorized` (apiClient.ts:205-212): корпоративные хосты не
 * проверяются никогда, остальные — по каналу (`dev` не проверяет).
 */
export function shouldRejectUnauthorized(url) {
  try {
    const { hostname } = new URL(url)
    if (TRUSTED_HOST_RE.test(hostname)) return false
  } catch {
    // неразбираемый URL — идём по каналу, как в desktop
  }
  return (process.env.OPENCODE_CHANNEL ?? "dev") !== "dev"
}

export function stringify(value) {
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value) ?? String(value)
  } catch {
    return String(value)
  }
}

/**
 * undici из окружения бандла sidecar'а.
 *
 * В обвязке undici нет в зависимостях, но он лежит рядом с бандлом
 * (createRequire от dist/node/node.js). Без него остаётся глобальный fetch —
 * тогда лимит заголовков снимает только флаг `--max-http-header-size`,
 * который ставит Python-родитель при спавне.
 */
export function loadUndici(modulePath) {
  if (!modulePath) return undefined
  try {
    return createRequire(pathToFileURL(modulePath))("undici")
  } catch (err) {
    console.error(`[launcher] undici рядом с бандлом недоступен, остаётся глобальный fetch: ${stringify(err)}`)
    return undefined
  }
}

/**
 * `undici.fetch` с нашим диспетчером.
 *
 * npm-undici не принимает глобальный `Request` (`ERR_INVALID_URL`), поэтому
 * Request разбирается на URL + init — как `resolveUrl` в desktop apiFetch.
 */
function fetchWithDispatcher(undici, dispatcher, input, init) {
  if (!(input instanceof Request)) return undici.fetch(input, { ...(init ?? {}), dispatcher })

  const request = {
    dispatcher,
    method: init?.method ?? input.method,
    headers: init?.headers ?? input.headers,
    signal: init?.signal ?? input.signal,
  }
  const body = init?.body ?? (input.body ? input.arrayBuffer() : undefined)
  if (!(body instanceof Promise)) return undici.fetch(input.url, body === undefined ? request : { ...request, body })
  return body.then((buffer) => undici.fetch(input.url, { ...request, body: buffer }))
}

export function makeUserFetch(undici) {
  const cookieFile = (process.env.WSC_SESSION_COOKIES_FILE ?? "").trim()
  let cookie = (process.env.WSC_SESSION_COOKIES ?? "").trim()
  const origin = (process.env.WSC_SHARED_API_ORIGIN ?? "").trim().replace(/\/+$/, "")
  const userAgent = (process.env.WSC_USER_AGENT ?? "").trim() || DEFAULT_USER_AGENT

  // Куки обновляет Python после re-login: в desktop их подменяет apiFetch,
  // здесь — перечитываем файл, чтобы MCP/поиск переживали истечение сессии.
  const readCookie = () => {
    if (cookieFile) {
      try {
        const fresh = readFileSync(cookieFile, "utf8").trim()
        if (fresh) cookie = fresh
      } catch {
        // файла ещё нет — остаёмся на стартовом значении
      }
    }
    return cookie
  }

  if (!readCookie()) return undefined

  const isLocal = (url) => {
    try {
      const host = new URL(url).hostname
      return host === "127.0.0.1" || host === "localhost" || host === "::1" || host === "[::1]"
    } catch {
      return false
    }
  }

  // Два агента с общим пулом соединений — как `agents` в apiClient.ts:220-231:
  // строгий и нестрогий (для корпоративных/dev-хостов).
  const agents = new Map()
  const dispatcherFor = (url) => {
    if (!undici) return undefined
    const reject = shouldRejectUnauthorized(url)
    let agent = agents.get(reject)
    if (!agent) {
      agent = reject
        ? new undici.Agent({ maxHeaderSize: MAX_HEADER_SIZE })
        : new undici.Agent({ maxHeaderSize: MAX_HEADER_SIZE, connect: { rejectUnauthorized: false } })
      agents.set(reject, agent)
    }
    return agent
  }
  const doFetch = (url, input, init) =>
    undici ? fetchWithDispatcher(undici, dispatcherFor(url), input, init) : fetch(input, init)

  return (input, init) => {
    const url =
      typeof input === "string" ? input : input instanceof URL ? input.toString() : String(input?.url ?? "")
    if (!url || isLocal(url)) return doFetch(url, input, init)

    const headers = new Headers(init?.headers ?? undefined)
    if (input instanceof Request) {
      input.headers.forEach((value, key) => {
        if (!headers.has(key)) headers.set(key, value)
      })
    }
    if (!headers.has("cookie")) headers.set("cookie", readCookie())
    if (!headers.has("user-agent")) headers.set("user-agent", userAgent)
    if (origin) {
      if (!headers.has("origin")) headers.set("origin", origin)
      if (!headers.has("referer")) headers.set("referer", `${origin}/`)
    }
    const next = { ...(init ?? {}), headers }
    if (input instanceof Request) return doFetch(url, new Request(input, next))
    return doFetch(url, input, next)
  }
}