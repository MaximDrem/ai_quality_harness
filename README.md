# lib-ai-quality-harness

Harness прогоняет YAML-кейсы через headless OpenCode, собирает файлы и финальные ответы в единые объекты `Artifact`, выполняет выбранные проверки и формирует XLSX/TXT-результаты.

Практическая инструкция — `ADD_TEST_CASES.md`. Корневая спецификация — `openspec/specs/lib-ai-quality-harness/spec.md` (OpenSpec 1.6.0).

## Запуск

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

PYTHONPATH=. .venv/bin/python -m example.run_test_agent --list-scenarios
PYTHONPATH=. .venv/bin/python -m example.run_test_agent \
  --scenario presentations --id GD-001 --runs 1 \
  --model provider/model --auth-mode ift --skills-dir /path/to/config

PYTHONPATH=. .venv/bin/python -m example.run_test_agent \
  --scenario presentations --validate /path/to/deck.pptx --case GD-001
```

- `--scenario` — каталог внутри `example/data`; по умолчанию `presentations`.
- `--auth-mode {ift,prod,cert}` — режим клиента; `prod` использует Electron-вход.
- `--skills-dir` — `OPENCODE_CONFIG_DIR` с `skills/`, `agent/` и настройками.
- `--validate` не запускает OpenCode и не обновляет cookies.
- Полный запуск требует внешний `desktop-ai-app`, `bun`, Node >= 22 и авторизацию стенда. Подробнее — `client/README.md`.

## Структура

```text
example/
  data/<scenario>/<case>.yaml
  metrics/<scenario>/<group>.yaml
  checks/
    llm/
      presentation/
        prompts.py
        schema.py
      <scenario>/
    processors/processor_<type>.py
    functional/
      path/
      text/
      rendered_photos/
      tool_calls/
      pptx/
        raw_structure/
        rendered_photos/
    _helpers.py
```

Сценарии обнаруживаются автоматически. Метрики находятся рядом с кейсами логически, но отдельно от кода проверок. Новый обработчик или процессор не требует центральной регистрации.

## YAML

Простой кейс:

```yaml
id: GD-001
user_phrase: Создай презентацию по заданию.
skill: presentation
artifact_type: .pptx
metric_groups: [sanity, readability]
```

Тип файла автоматически выбирает процессор. Поле `processor` нужно только для нестандартного override.

Группа метрик:

```yaml
id: readability
phase: universal
weight: 1
checks:
  - kind: function
    metric: Читаемость
    handler: pptx/rendered_photos/text_background_contrast
    artifacts: [final]
    text: Проверка контраста
```

Вес можно задать также отдельной проверке в кейсе. Отсутствующий вес равен `1`; перед расчётом веса нормализуются до суммы `1`. Конфликтующие веса одной метрики отклоняются.

## Профиль judge

Промпты и structured output выбираются по `--scenario`. Текущий профиль находится в `checks/llm/presentation/`; `presentations` является его alias. Новый сценарий добавляет `checks/llm/<scenario>/__init__.py` с `SYSTEM_PROMPT`, `build_text_prompt`, `build_vision_prompt` и `JudgeResult`. Изменять runner не требуется.

## Единый Artifact

```text
Artifact
  name, role, path, artifact_type
  text
  raw_structure
  rendered_photos
  tool_calls
  results
```

Процессор заполняет нужные атрибуты через `process(artifact, attributes)`. Материализация выполняется один раз и только для атрибутов, необходимых выбранным проверкам.

Каталог функционального handler определяет основной атрибут:

- `path/` → `artifact.path`;
- `text/` → `artifact.text`;
- сегмент `raw_structure` → `artifact.raw_structure`;
- `rendered_photos/` → `artifact.rendered_photos`;
- `tool_calls/` → `artifact.tool_calls` с input/output/error и терминальным состоянием.

Дополнительные атрибуты объявляются `REQUIRES` в модуле. Результат проверки добавляется в `artifact.results` и затем попадает в XLSX.

Типов два: `function` для Python и `llm` для judge. Для judge обязателен `input: text | raw_structure | rendered_photos | tool_call`. Процессор сериализует `raw_structure` в JSON-представление для judge.

Существующие процессоры переиспользуются по расширению. Для нового формата добавляется `checks/processors/processor_<name>.py` без изменений evaluator.

## Вложения и артефакты

```yaml
attachments:
  - name: source
    path: assets/source.pptx

artifacts:
  - name: extracted
    stage: intermediate
    type: .md
    path: "*-ref/*.md"
    required: true
  - name: deck
    stage: final
    type: .pptx
    path: "output/*.pptx"
    required: true
```

- Пути вложений считаются от каталога сценария и передаются только с первым сообщением.
- Пути/glob артефактов считаются от `work_dir`.
- Проверка выбирает `source`, `intermediate`, `final`, `final_message` или конкретное имя.
- Промежуточные версии сохраняются в snapshot’ах с `origin_path` и `event_index`.
- Для прекращения «Продолжить» нужны все обязательные финалы.

Файловый финал сначала ищется по пути из завершённого assistant-ответа. При отсутствии подходящего пути выполняется рекурсивный поиск. `uploads/` и исходные attachments исключены из финальных кандидатов.

## Финальный ответ как результат

Для сценария без файла:

```yaml
artifacts:
  - name: final_message
    stage: final
    kind: message
    required: true
```

Финальный assistant-текст становится `Artifact` с `kind=message`, `path=None` и текстом ответа. К нему работают function и llm-проверки через селектор `final_message`.

## Результаты

```text
runs/<timestamp>_<opencode_model>/
  results.xlsx
  one_number_result.txt
  run_<n>/<case_id>/
    raw.log
    agent.log
    intermediate_artifacts/
    render_artifact/
```

- `raw.log` — полный технический поток событий.
- `agent.log` — компактная человекочитаемая последовательность.
- XLSX содержит проверки, отдельные routing, список tools, время, суммарные токены и фактический контекст.
- Routing сравнивает завершённый skill-вызов с `skill` кейса и не участвует в `S`.
- `one_number_result.txt` содержит `S_case` для каждого кейса/run и `S_scenario` как среднее валидных `S_case`. Веса нормализуются внутри каждого кейса отдельно. Routing, среднее время и максимальный контекст выводятся отдельно.
- Ошибки judge не входят в `pass/all`; при отсутствии достоверных метрик `S=NA`.

## Offline-проверки

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3.12 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3.12 -m example.run_test_agent --help
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3.12 -m example.run_test_agent --list-scenarios
```

OpenCode, реальная обработка вложений и VLLM как smoke-test не используются.
