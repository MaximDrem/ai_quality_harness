// Прод-логин в СУДИР через настоящий Electron — порт `sudir-auth.ts`
// (desktop-ai-app/packages/desktop-electron/src/main/sudir-auth.ts).
//
// Что взято из desktop как референс (не импорт — другой рантайм и нет
// electron-vite):
//   * BrowserWindow на SHARED_API_ORIGIN с in-memory партицией
//     `sudir-auth-<ts>` (без `persist:` — куки и выбор сертификата не
//     переживают окно, sudir-auth.ts:35-38);
//   * клиентский сертификат из хранилища ОС: обработчик
//     `select-client-certificate` (sudir-auth.ts:83-107). При одном
//     сертификате Electron выбирает его сам, при нескольких — берём первый
//     (в desktop там picker, у headless-клиента его нет);
//   * сбор ВСЕХ кук сессии после возврата на origin
//     (`session.cookies.get({})`, sudir-auth.ts:154-160);
//   * снятие реальных заголовков браузера (`onBeforeSendHeaders`,
//     sudir-auth.ts:125-141) — часть IAM-прокси отвечает 400 клиенту без
//     User-Agent/Origin/Referer;
//   * проверка `/session-check` == 200 (sudir-auth.ts:190).
//
// Запуск: electron client/server/electron_login.cjs
// Env: WSC_ELECTRON_URL (origin), WSC_ELECTRON_OUT (JSON-файл результата),
//      WSC_ELECTRON_TIMEOUT_MS, WSC_ELECTRON_HIDDEN=1, WSC_ELECTRON_CHANNEL.
// Результат пишется в WSC_ELECTRON_OUT и завершает процесс: Python-обёртка
// (client/auth/electron_login.py) читает его и проверяет сессию ещё раз.

const fs = require("node:fs")
const { app, BrowserWindow, dialog } = require("electron")

const ORIGIN = (process.env.WSC_ELECTRON_URL || "").trim()
const OUT_PATH = (process.env.WSC_ELECTRON_OUT || "").trim()
const TIMEOUT_MS = Number(process.env.WSC_ELECTRON_TIMEOUT_MS || 300000)
const HIDDEN = process.env.WSC_ELECTRON_HIDDEN === "1"
const CHANNEL = process.env.WSC_ELECTRON_CHANNEL || "prod"

const rootUrl = ORIGIN.split("?")[0].split("#")[0].replace(/\/+$/, "")
const checkUrl = `${rootUrl}/session-check`

let finished = false
let writeResult = () => {}

function finish(code) {
  if (finished) return
  finished = true
  try {
    writeResult()
  } finally {
    app.exit(code)
  }
}

// Как desktop (app.ts:99-101): loopback не должен уходить в корпоративный прокси.
app.commandLine.appendSwitch("proxy-bypass-list", "<loopback>")

// Как desktop (app.ts:102-115): доверяем корпоративным корневым CA, иначе
// Electron отвергнет TLS внутренних стендов. В dev-канале — всем (dev-стенды).
app.on("certificate-error", (event, _webContents, url, _error, _certificate, callback) => {
  try {
    const host = new URL(url).hostname
    const trusted =
      CHANNEL === "dev" ||
      /\.(sberbank|sbrf)(\.\w+)?$/i.test(host) ||
      host === "localhost" ||
      host === "127.0.0.1"
    if (trusted) {
      event.preventDefault()
      callback(true)
      return
    }
  } catch {
    /* кривой URL — отдаём решение Chromium */
  }
  callback(false)
})

async function probeSession(ses, win) {
  try {
    if (typeof ses.fetch === "function") {
      const res = await ses.fetch(checkUrl, { method: "GET", redirect: "manual" })
      return res.status
    }
    return await win.webContents.executeJavaScript(
      `fetch(${JSON.stringify(checkUrl)}, { redirect: "manual" }).then((r) => r.status).catch(() => 0)`,
    )
  } catch (err) {
    console.error(`[electron-login] session-check error: ${err}`)
    return 0
  }
}

async function main() {
  if (!OUT_PATH) {
    console.error("[electron-login] WSC_ELECTRON_OUT не задан")
    app.exit(2)
    return
  }

  const result = {
    ok: false,
    origin: rootUrl,
    cookies: [],
    cookieDomains: {},
    headers: {},
    sessionCheckStatus: 0,
    error: null,
    startedAt: new Date().toISOString(),
  }

  writeResult = () => {
    try {
      const tmp = `${OUT_PATH}.tmp`
      fs.writeFileSync(tmp, JSON.stringify(result, null, 2), { mode: 0o600 })
      fs.renameSync(tmp, OUT_PATH)
    } catch (err) {
      console.error(`[electron-login] не удалось записать результат: ${err}`)
    }
  }

  if (!rootUrl) {
    result.error = "WSC_ELECTRON_URL не задан"
    console.error(`[electron-login] ${result.error}`)
    finish(2)
    return
  }

  console.error(`[electron-login] origin=${rootUrl} hidden=${HIDDEN} channel=${CHANNEL}`)

  // Та же in-memory партиция, что в sudir-auth.ts:35-38: ничего не пишется на
  // диск, сессия умирает вместе с окном.
  const sessionId = `sudir-auth-${Date.now()}`
  const win = new BrowserWindow({
    width: 480,
    height: 640,
    show: false,
    modal: false,
    autoHideMenuBar: true,
    title: "Аутентификация пользователя",
    frame: true,
    closable: true,
    maximizable: false,
    minimizable: false,
    fullscreenable: false,
    backgroundColor: "#101010",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      partition: sessionId,
    },
  })

  const ses = win.webContents.session

  // Идентификатор окна в логах: прогонов может быть несколько подряд.
  console.error(`[electron-login] partition=${sessionId}`)

  // Заголовок окна фиксирован: IdP переименовывает окно при загрузке.
  win.on("page-title-updated", (event) => event.preventDefault())

  // mTLS: ровно как sudir-auth.ts:83-107 — при ОДНОМ сертификате не
  // вмешиваемся (Electron выбирает единственный сам), при нескольких даём
  // человеку выбрать. В desktop это HTML-picker в окне логина; здесь —
  // нативный диалог, потому что renderer'а у лончера нет. Никаких «взять
  // первый»: сертификат определяет, от чьего имени пройдёт вход.
  win.webContents.on("select-client-certificate", (event, _url, certificates, callback) => {
    console.error(`[electron-login] select-client-certificate: ${certificates.length} шт.`)
    if (certificates.length < 2) return

    event.preventDefault()
    const buttons = certificates.map(
      (cert, i) => `${i + 1}. ${cert.subjectName || "Unknown"}`,
    )
    buttons.push("Отмена")
    const detail = certificates
      .map((cert, i) => `${i + 1}. ${cert.subjectName || "Unknown"}\n   issuer: ${cert.issuerName || "Unknown"}`)
      .join("\n")
    const choice = dialog.showMessageBoxSync(win, {
      type: "question",
      title: "Выбор сертификата",
      message: "Выберите клиентский сертификат для входа",
      detail,
      buttons,
      defaultId: 0,
      cancelId: buttons.length - 1,
      noLink: true,
    })
    const selected = choice >= 0 && choice < certificates.length ? certificates[choice] : undefined
    console.error(
      `[electron-login] выбран: ${selected ? selected.subjectName || "Unknown" : "отмена"}`,
    )
    callback(selected)
  })

  // Снимаем заголовки реального браузерного запроса к целевому хосту
  // (sudir-auth.ts:125-141): их потом можно переиграть в API-запросах.
  win.webContents.session.webRequest.onBeforeSendHeaders((details, callback) => {
    if (details.url.startsWith(rootUrl)) {
      result.headers = { ...details.requestHeaders }
    }
    callback({ requestHeaders: details.requestHeaders })
  })

  win.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL) => {
    // -3 = ERR_ABORTED: обычный редирект/отмена, не ошибка логина.
    if (errorCode === -3) return
    console.error(`[electron-login] did-fail-load ${errorCode} ${errorDescription} ${validatedURL}`)
    result.error = `did-fail-load ${errorCode} ${errorDescription}`
    finish(1)
  })

  win.on("closed", () => {
    if (!finished) {
      result.error = result.error || "окно закрыто пользователем до завершения входа"
      console.error(`[electron-login] ${result.error}`)
      finish(1)
    }
  })

  const timer = setTimeout(() => {
    result.error = `таймаут ${TIMEOUT_MS} мс: вход не завершён`
    console.error(`[electron-login] ${result.error}`)
    finish(1)
  }, TIMEOUT_MS)
  timer.unref?.()

  win.webContents.on("dom-ready", async () => {
    const currentUrl = win.webContents.getURL()
    if (!currentUrl.startsWith(rootUrl)) return
    if (finished) return

    try {
      const cookies = await ses.cookies.get({})
      result.cookies = cookies
      result.finalUrl = currentUrl

      const domains = {}
      for (const c of cookies) domains[c.domain || "<host-only>"] = (domains[c.domain || "<host-only>"] || 0) + 1
      result.cookieDomains = domains
      console.error(`[electron-login] cookies: ${cookies.length} (${JSON.stringify(domains)})`)

      result.sessionCheckStatus = await probeSession(ses, win)
      console.error(`[electron-login] /session-check -> ${result.sessionCheckStatus}`)
      result.ok = result.sessionCheckStatus === 200
      if (!result.ok) result.error = `session-check -> ${result.sessionCheckStatus}`
    } catch (err) {
      result.error = String(err)
      console.error(`[electron-login] ${result.error}`)
    }

    finish(result.ok ? 0 : 1)
  })

  console.error("[electron-login] loadURL")
  win.loadURL(rootUrl).catch((err) => {
    result.error = `loadURL: ${err}`
    console.error(`[electron-login] ${result.error}`)
    finish(1)
  })

  win.once("ready-to-show", () => {
    if (!HIDDEN && !win.isDestroyed()) win.show()
  })
}

app.whenReady().then(main).catch((err) => {
  console.error(`[electron-login] fatal: ${err && err.stack ? err.stack : err}`)
  app.exit(1)
})