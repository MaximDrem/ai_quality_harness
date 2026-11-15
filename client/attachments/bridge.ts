// Мост к бэкенду desktop-ai-app: превращает «черновик» сообщения
// (текст + приложенные файлы, как их держит UI) в те же части сообщения,
// которые UI отправил бы в `/session/:id/prompt_async`.
//
// Почему мост, а не переписывание на Python: материализация вложений живёт
// в `SessionPrompt.resolvePart` (src/session/prompt.ts) и не экспортирована.
// Поэтому модуль открывает настоящий `SessionPrompt.prompt` в headless-режиме
// (`noReply: true`) со стабами сервисов: реальное тело `resolvePart` читает
// файлы с диска, копирует их в `uploads/<session>/<message>/`, извлекает текст
// Office и отдаёт итоговые части, а стаб `Session.updatePart` их перехватывает.
//
// Запуск (cwd — packages/opencode, чтобы работали алиасы `@/`):
//   bun run --conditions=browser <этот файл> --in <job.json> --out <result.json>
//
// Модули бэкенда лежат в packages/opencode и импортируются по относительным
// путям от каталога запуска (cwd = packages/opencode). Алиасы `@/...` внутри
// самих модулей разрешает tsconfig packages/opencode — отдельно объявлять их
// не нужно.
import { existsSync, readFileSync, writeFileSync } from "node:fs"
import { fileURLToPath } from "node:url"

const src = (rel: string) => `${process.cwd()}/src/${rel}`
// Пакеты монорепозитория (`@opencode-ai/*`) резолвятся из каталога запуска —
// bun ищет их в node_modules рядом с мостом, а там их нет.
const pkg = (rel: string) => `${process.cwd()}/../${rel}`
// Пакеты из node_modules бэкенда. Импортировать их по голому имени нельзя:
// тогда bun выдаёт один и тот же модуль дважды — свою копию от имени моста и
// копию бэкенда, и Context.Tag из `effect` перестают совпадать
// (`undefined is not an object (evaluating 'root._fiberCache')`).
const dep = (rel: string) => fileURLToPath(new URL(`./node_modules/${rel}`, `file://${process.cwd()}/`))

type FilePartInput = {
  id?: string
  type: "file"
  mime: string
  filename?: string
  url: string
  ignored?: boolean
  localPath?: string
}

type TextPartInput = {
  id?: string
  type: "text"
  text: string
  synthetic?: boolean
}

type Job = {
  sessionID: string
  messageID: string
  workDir: string
  baseDir?: string | null
  agent?: string
  model?: { providerID: string; modelID: string } | null
  parts: Array<TextPartInput | FilePartInput>
}

function arg(name: string): string {
  const i = process.argv.indexOf(name)
  if (i === -1 || i + 1 >= process.argv.length) throw new Error(`missing ${name}`)
  return process.argv[i + 1]
}

const inPath = arg("--in")
const outPath = arg("--out")
if (!existsSync(inPath)) throw new Error(`job file not found: ${inPath}`)
const job: Job = JSON.parse(readFileSync(inPath, "utf8"))
if (job.baseDir) process.env.OPENCODE_BASE_DIR = job.baseDir

// Импорты после выставления OPENCODE_BASE_DIR: Global.Path читается при
// загрузке модуля.
const { Effect, Layer, Option } = await import(dep("effect/dist/index.js"))
const { SessionPrompt } = await import(src("session/prompt"))
const { Bus } = await import(src("bus"))
const { SessionStatus } = await import(src("session/status"))
const { Session } = await import(src("session/session"))
const { Agent } = await import(src("agent/agent"))
const { Provider } = await import(src("provider/provider"))
const { SessionProcessor } = await import(src("session/processor"))
const { SessionCompaction } = await import(src("session/compaction"))
const { Plugin } = await import(src("plugin"))
const { Command } = await import(src("command"))
const { Config } = await import(src("config/config"))
const { Permission } = await import(src("permission"))
const { MCP } = await import(src("mcp"))
const { LSP } = await import(src("lsp/lsp"))
const { ToolRegistry } = await import(src("tool/registry"))
const { Truncate } = await import(src("tool/truncate"))
const { Instruction } = await import(src("session/instruction"))
const { SessionRunState } = await import(src("session/run-state"))
const { SessionRevert } = await import(src("session/revert"))
const { SessionSummary } = await import(src("session/summary"))
const { SystemPrompt } = await import(src("session/system"))
const { LLM } = await import(src("session/llm"))
const { SessionCompletionReviewer } = await import(src("session/completion-reviewer"))
const { Snapshot } = await import(src("snapshot"))
const { ImagePreview } = await import(src("image-preview"))
const { AppFileSystem } = await import(pkg("core/src/filesystem"))
const { CrossSpawnSpawner } = await import(pkg("core/src/cross-spawn-spawner"))
const MessageV2 = await import(src("session/message-v2"))

const model: { providerID: string; modelID: string } =
  job.model ?? { providerID: "gigachat", modelID: "GIGACHAT_MODEL_UNKNOWN" }

const sessionInfo: any = {
  id: job.sessionID,
  projectID: "global",
  directory: job.workDir,
  worktree: job.workDir,
  title: "attachment bridge",
  version: "0",
  time: { created: Date.now(), updated: Date.now() },
  agent: job.agent ?? "build",
  model: { providerID: model.providerID, modelID: model.modelID },
}

const saved: any[] = []
const savedMessage: any[] = []

const logged = process.env.BRIDGE_DEBUG === "1"

function proxy(name: string, impl: Record<string, (...a: any[]) => any>) {
  return new Proxy(
    {},
    {
      get(_target, prop: string | symbol) {
        if (typeof prop !== "string") return undefined
        if (prop === "then") return undefined
        if (impl[prop])
          return (...args: any[]) => {
            if (logged) console.error(`[bridge] ${name}.${prop}`)
            return impl[prop](...args)
          }
        return (..._args: any[]) => {
          if (prop === "pipe" || prop === "toString") return () => Effect.void
          if (logged) console.error(`[bridge] ${name}.${prop} MISSING`)
          return Effect.die(`${name}.${prop} is not available in the attachment bridge`)
        }
      },
    },
  )
}

const sessionStub = proxy("session", {
  get: () => Effect.succeed(sessionInfo),
  updateMessage: (m: any) => {
    savedMessage.push(m)
    return Effect.succeed(m)
  },
  updatePart: (p: any) => {
    // Приложение читает части сообщения из БД (`sessions.messages`), а там
    // они упорядочены по id: части, сгенерированные позже (header
    // материализации), идут последними. Порядок вызовов `updatePart` для
    // этого не годится — `resolvePart` выполняется параллельно.
    saved.push(p)
    return Effect.succeed(p)
  },
  setPermission: () => Effect.void,
  touch: () => Effect.void,
  findMessage: () => Effect.succeed(Option.none()),
})

const agentInfo = {
  name: job.agent ?? "build",
  permission: [],
  model: { providerID: model.providerID, modelID: model.modelID },
}

// Файловая система — настоящая: `resolvePart` читает файлы по `file://`
// и `data:`-ссылкам и пишет копии в uploads. В headless-режиме нет
// платформенного слоя `FileSystem`, поэтому нужные операции реализованы на
// node:fs и отданы AppFileSystem как есть.
const { readFile, writeFile, mkdir, stat, readdir, chmod, rm } = await import("node:fs/promises")
const { dirname } = await import("node:path")
const { FileSystem } = await import(dep("effect/dist/FileSystem.js"))
const nodeFs = {
  exists: (p: string) =>
    Effect.promise(() => stat(p).then(() => true, () => false)),
  stat: (p: string) =>
    Effect.promise(() => stat(p)).pipe(
      Effect.map((s: any) => ({
        type: s.isDirectory() ? "Directory" : s.isFile() ? "File" : s.isSymbolicLink() ? "SymbolicLink" : "Other",
      })),
    ),
  readFile: (p: string) => Effect.promise(() => readFile(p)),
  readFileString: (p: string) => Effect.promise(() => readFile(p, "utf8")),
  // Запись создаёт родительский каталог: бэкенд пишет вложения сразу в
  // uploads/<session>/<message>/, полагаясь на платформенный слой, который
  // умеет до-создавать каталоги.
  writeFile: (p: string, data: any) =>
    Effect.promise(async () => {
      await mkdir(dirname(p), { recursive: true })
      await writeFile(p, data)
    }),
  writeFileString: (p: string, data: string) =>
    Effect.promise(async () => {
      await mkdir(dirname(p), { recursive: true })
      await writeFile(p, data, "utf8")
    }),
  makeDirectory: (p: string, opts?: { recursive?: boolean }) => Effect.promise(() => mkdir(p, opts)),
  readDirectory: (p: string) => Effect.promise(() => readdir(p)),
  chmod: (p: string, mode: number) => Effect.promise(() => chmod(p, mode)),
  remove: (p: string) => Effect.promise(() => rm(p, { recursive: true, force: true })),
}
const fileSystemLayer = AppFileSystem.layer.pipe(Layer.provide(Layer.succeed(FileSystem, nodeFs as any)))

const stubs: Layer.Layer<any>[] = [
  Layer.succeed(Bus.Service, Bus.Service.of({ publish: () => Effect.void } as any)),
  Layer.succeed(Session.Service, Session.Service.of(sessionStub as any)),
  Layer.succeed(SessionStatus.Service, SessionStatus.Service.of({} as any)),
  Layer.succeed(ImagePreview.Service, ImagePreview.Service.of({} as any)),
  Layer.succeed(Snapshot.Service, Snapshot.Service.of({} as any)),
  Layer.succeed(
    Agent.Service,
    Agent.Service.of(
      proxy("agent", {
        get: () => Effect.succeed(agentInfo),
        list: () => Effect.succeed([agentInfo]),
        defaultAgent: () => Effect.succeed(agentInfo.name),
      }) as any,
    ),
  ),
  Layer.succeed(
    Provider.Service,
    Provider.Service.of(
      proxy("provider", {
        getModel: () => Effect.die("provider.getModel is not available in the attachment bridge"),
        defaultModel: () => Effect.succeed({ providerID: model.providerID, modelID: model.modelID }),
      }) as any,
    ),
  ),
  fileSystemLayer,
  Layer.succeed(SessionRevert.Service, SessionRevert.Service.of(proxy("revert", { cleanup: () => Effect.void }) as any)),
  Layer.succeed(
    Instruction.Service,
    Instruction.Service.of(proxy("instruction", { clear: () => Effect.void }) as any),
  ),
  Layer.succeed(Plugin.Service, Plugin.Service.of(proxy("plugin", { trigger: () => Effect.succeed(undefined) }) as any)),
  ...[
    SessionProcessor,
    SessionCompaction,
    Command,
    Config,
    Permission,
    MCP,
    LSP,
    ToolRegistry,
    Truncate,
    SessionRunState,
    SessionSummary,
    SystemPrompt,
    LLM,
    SessionCompletionReviewer,
  ].map((m: any) => Layer.succeed(m.Service, m.Service.of({} as any))),
]

const program = Effect.gen(function* () {
  const prompt = yield* SessionPrompt.Service
  const message = yield* prompt.prompt({
    sessionID: job.sessionID,
    messageID: job.messageID,
    agent: job.agent ?? "build",
    model,
    noReply: true,
    parts: job.parts,
  } as any)
  return message
})

try {
  const message: any = await Effect.runPromise(
    program.pipe(
      Effect.provide(SessionPrompt.layer),
      ...stubs.map((l) => Effect.provide(l as any)),
      Effect.provide(CrossSpawnSpawner.defaultLayer as any),
    ),
  )
  // Порядок как при чтении из БД: части упорядочены по id. Так их видит
  // модель в реальном приложении — header материализации, получивший id
  // последним, оказывается после данных вложения.
  const ordered = [...saved]
    .filter((p) => p && typeof p === "object")
    .sort((a, b) => String(a.id ?? "").localeCompare(String(b.id ?? "")))
  const parts = ordered.map((p) => {
    const copy = { ...p }
    delete copy.sessionID
    delete copy.messageID
    return copy
  })
  // Второй шаг того же конвейера: собрать сообщения для провайдера функцией
  // бэкенда `toModelMessages` — это уже то, что уходит в запрос к модели
  // (текстовые части склеиваются, `ignored` отбрасывается, файлы становятся
  // file-частями). Модель берётся из каталога провайдеров по id; если её там
  // нет, используется минимальная заглушка (тогда теряются только
  // параметры конкретной модели, не содержимое).
  const modelMessages = await Effect.runPromise(
    Effect.gen(function* () {
      const provider = yield* Provider.Service
      const found = yield* provider.getModel(model.providerID, model.modelID).pipe(Effect.exit)
      const resolved =
        found._tag === "Success" ? found.value : {
          providerID: model.providerID,
          id: model.modelID,
          api: { id: model.modelID, npm: "@ai-sdk/openai-compatible" },
          capabilities: { input: {} },
          options: {},
        }
      return yield* Effect.tryPromise(() =>
        MessageV2.toModelMessages(
          [{ info: { ...(message as any).info, id: job.messageID }, parts: ordered } as any],
          resolved as any,
        ),
      )
    }).pipe(Effect.provide(Provider.defaultLayer), Effect.orElseSucceed(() => null as any)),
  ).catch(() => null)

  writeFileSync(
    outPath,
    JSON.stringify(
      {
        ok: true,
        sessionID: job.sessionID,
        messageID: job.messageID,
        messages: savedMessage,
        parts,
        returned: message?.parts ?? null,
        model_messages: modelMessages,
      },
      null,
      1,
    ),
  )
  console.log("bridge ok")
} catch (error: any) {
  const text = error?.message ?? String(error)
  writeFileSync(outPath, JSON.stringify({ ok: false, error: text }))
  console.error(text)
  process.exit(1)
}