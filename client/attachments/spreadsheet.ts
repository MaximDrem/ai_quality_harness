// xlsx/xls -> CSV тем же пакетом, что и UI (`@datalens-tech/xlsx`), чтобы
// клиент получал ровно тот CSV, который desktop кладёт в CSV-«пилюлю».
//
// Печатает JSON {"csv": "..."} в stdout; cwd — каталог desktop-ai-app, откуда
// грузится пакет (в test_agent его node_modules нет).
//
// Использование: bun run spreadsheet.ts <file.xlsx> <cwd с node_modules> [пакет]
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { join } from "node:path"

const [target, modulesCwd, packageName = "@datalens-tech/xlsx"] = process.argv.slice(2)
if (!target) {
  console.error("usage: bun run spreadsheet.ts <file.xlsx> <cwd> [package]")
  process.exit(2)
}

const XLSX = await import(createRequire(join(modulesCwd || process.cwd(), "index.js")).resolve(packageName))
const reader = XLSX.default ?? XLSX

const buffer = readFileSync(target)
const workbook = reader.read(new Uint8Array(buffer), { type: "array" })

// Все листы: реальная книга часто раскладывает факты по вкладкам
// (сводка / детали / справочник), и модели нужны все. Заголовок листа
// добавляется, когда вкладок больше одной.
const sections: string[] = []
for (const sheetName of workbook.SheetNames) {
  const csv = String(reader.utils.sheet_to_csv(workbook.Sheets[sheetName])).trimEnd()
  if (!csv) continue
  sections.push(workbook.SheetNames.length > 1 ? `# Sheet: ${sheetName}\n${csv}` : csv)
}

console.log(JSON.stringify({ csv: sections.join("\n\n") }))