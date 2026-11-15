# ai-quality-harness

Прогоняет тест-кейсы через headless-бэкенда
GigaCat, собирает созданные агентом артефакты (.pptx/.docx/.xlsx/.pdf),
рендерит их в PNG и оценивает двумя способами:

- **функциональные проверки** — кодом, без LLM (`example/checks/functional/`):
  существование файла, рендер, контраст текста/фона, пустые зоны и тестовые контейнеры,
  `style_consistency`, `slide_count`, `repeated_words` и др.;
- **судья-LLM** — бинарные (PASS/FAIL) проверки по тексту артефакта (`text`)
  и по отрендеренным изображениям (`vlm`) через OpenAI-совместимый vLLM
  endpoint. Градуированных оценок (score/шкал) намеренно нет: схема
  `JudgeResult` — только observations/defect_slides/success/reason.

Все комментарии и docstring'и в коде — на русском.

## Быстрый старт

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Проверка готового файла без агента (единственный standalone-режим):
PYTHONPATH=. .venv/bin/python -m example.run_test_agent \
    --validate PATH_TO_ARTIFACT [--case GD-013] [--checks "..."]

# Полный прогон (требует client/ и валидные куки):
PYTHONPATH=. python3.12 -m example.run_test_agent [--id GD-001] [--model provider/model] [--runs N]
```

Ключи: `--id` — один кейс; `--model` — модель `provider/model`;
`--auth-mode {ift,prod,cert}` — способ авторизации инстанса (по умолчанию
`ift`, он же `GIGATOOL_AUTH_MODE`);
`--list-models` — список моделей; `--runs N` — повторов датасета;
`--case` — чей чек-лист брать для `--validate`; `--checks` — своё
переопределение (по одной text-проверке на строку).

## Структура

```
example/
  run_test_agent.py   — точка входа: CLI, режимы, сводка метрик
  config.py           — пути и константы, загрузка .env
  models.py           — data-модели: TestCase, Check, CheckResult, RunResult
  cases.py            — чтение data/*.yaml и checks/common/*.yaml, сборка чек-листа
  evaluate.py         — оценка артефакта: рендер + функциональные проверки + судья
  report.py           — итоговый results.xlsx
  logs.py             — raw.log (события агента) и сводный лог кейса
  agent/
    session.py        — прогон кейса: инстанс агента, события, «Продолжить»
    events.py         — разбор событий: скилы/тулы, артефакты, токены, ошибки
    cookies.py        — обновление Sudir-куков (~13 ч жизни)
  judge/
    client.py         — клиент vLLM (конфиг из env ПРИ ИМПОРТЕ — импорт ленивый)
    extract.py        — извлечение текста из pptx/docx/xlsx для text-проверок
    runner.py         — run_judge: параллельные проверки, посайдовые режимы
  render/
    office.py         — LibreOffice (headless) -> PDF -> PNG, пост-контроль
    fonts.py          — изоляция fontconfig, подмена отсутствующих шрифтов
    render_fonts/     — TTF-заменители (Carlito/Caladea/Liberation)
  checks/
    common/<тип>.yaml — общие проверки по типу файла (checks + universal)
    functional/       — функциональные проверки: один файл — одна проверка,
                        реестр имён в functional/__init__.py (_HANDLERS)
    llm/prompts.py    — промты судьи (данные, без чтения env)
    llm/schema.py     — схема structured output (JudgeResult)
  data/<case_id>.yaml — тест-кейсы: id, user_phrase, skill, artifact_type,
                        checks (ТОЛЬКО индивидуальные проверки кейса)
client/               — Модуль для подключения opencode
  agent/              — сессия и клиент: `ask`/`actions`, события, артефакты
  attachments/        — вложения в промпте: путь к файлу -> части сообщения
                        ровно такие, какие отправил бы UI desktop
  core/               — конфиг (`ClientConfig`), ошибки, генерация id
  http/               — HTTP-клиент к инстансу
runs/                 — результаты прогонов (создаётся автоматически)
```

## Поток данных

1. `cases.load_test_cases` читает `data/*.yaml`; `cases.build_check_list`
   собирает итоговый чек-лист: общие `checks` из `checks/common/<тип>.yaml` +
   индивидуальные из кейса (+ блок `universal` при полном прогоне и при
   `--validate --case`, с дедупом по полям проверки). Правило раскладки для
   pptx: в `checks` только функциональные проверки, все проверки судьи
   (vlm/text) живут в `universal` — наборы метрик
   Читаемость/Компоновка/Красота/Стиль/Текст одинаковы для любой колоды,
   и дефект не считается дважды.
2. `agent.session.run_case` поднимает headless-инстанс (client/), отправляет
   `user_phrase`, пишет события в `raw.log`; уточняющий вопрос агента — кейс
   дропается («Вопрос агента»), нет артефакта — до 7 сообщений «Продолжить».
3. Артефакты рендерятся в PNG (`render.office.render_office_file`).
4. Проверки: сначала функциональные (кодом), затем судья-LLМ
   (`judge.runner.run_judge`, text/vlm).
5. Итог: `runs/<stamp>/results.xlsx` + per-case логи и `result.xlsx`.

## Формат проверок

В yaml — только структура (многострочный `text: |-`, строки склеиваются
через пробел):

```yaml
- kind: vlm              # text | vlm | functional (по умолчанию text)
  metric: Читаемость     # метрика-группировка (необязательно)
  user_phrase: false     # передавать ли user_phrase кейса судье (по умолчанию true)
  single_slide: true     # судить по слайду за запрос, FAIL при первом упавшем
  text: |-
    ОЦЕНИВАЙ ТОЛЬКО: ...
```

## Как добавить

- **Тест-кейс**: новый `data/<case_id>.yaml` с индивидуальными проверками.
  Общие метрики туда не копировать — они подмешиваются из `checks/common/`.
- **Общую проверку судьи** (например «Стиль» для всех презентаций): в
  `checks/common/pptx.yaml`, блок `universal` (в `checks` — только
  функциональные проверки).
- **Функциональную проверку**: скрипт `checks/functional/<имя>.py` с единым
  интерфейсом `run(check, artifacts, photos, user_phrase) -> CheckResult`
  (хелперы ok/unavailable — в `_helpers.py`) + запись в `_HANDLERS` в
  `checks/functional/__init__.py`. В yaml: `kind: functional`, `text: <имя>`.

## Конфигурация

`.env` в корне репозитория.

## Результаты

Каждый запуск пишет в `runs/<yyyy-mm-dd_hh-mm>/`: `run_<n>/<case_id>/`
(`work_dir/`, `raw.log`, `render_artifact/`, `result.xlsx`), агрегат —
`runs/<stamp>/results.xlsx`. Колонки: кейс, проверка, тип, метрика,
PASSED/FAILED/ERROR, обоснование, статус, время агента, токены.

Строки с ошибкой судьи (обрезка ответа по токенам, таймаут) помечаются
`ERROR` + «ОШИБКА СУДЬИ» и из подсчёта провалов и метрик исключаются — это
не вердикт по артефакту.