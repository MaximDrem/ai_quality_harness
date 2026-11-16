# ai-quality-harness

Harness прогоняет YAML-кейсы через headless OpenCode, собирает конечные и промежуточные артефакты и оценивает их функциональными проверками и LLM-judge. Кейсы, метрики и Python-обработчики разделены: YAML определяет, какие проверки и процессоры применяются к каждому артефакту.

Подробное руководство по добавлению сценариев, YAML и Python-проверок — `ADD_TEST_CASES.md`. Корневая спецификация поведения проекта — `openspec/specs/lib-ai-quality-harness/spec.md` (OpenSpec 1.6.0).

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
- `--list-scenarios` — автоматически найденные сценарии.
- `--auth-mode {ift,prod,cert}` — режим `client`; `prod` использует Electron-вход.
- `--skills-dir` — `OPENCODE_CONFIG_DIR` с `skills/`, `agent/` и настройками.
- `--validate` не запускает OpenCode и не обновляет cookies. Для Office-рендера нужны LibreOffice и `pdftoppm`; judge настраивается через `VLLM_BASE_URL`, `VLLM_MODEL`, `VLLM_API_KEY`.
- Полный запуск требует внешний `desktop-ai-app`, `bun`, Node >= 22 и авторизацию стенда. Подробнее — `client/README.md`.

## Структура сценария и проверок

```text
example/
  data/sample_scenario/
    SAMPLE-001.yaml

  checks/
    metrics/sample_scenario/
      quality.yaml

    functional/
      _helpers.py
      common/
      office/
      pptx/
      text/
      sample_domain/
```

Новый сценарий появляется в `--list-scenarios`, если в `data/<scenario>/` есть YAML-кейсы. Изменять CLI, `cases.py` или реестры не нужно.

Пример кейса:

```yaml
id: SAMPLE-001
user_phrase: |-
  Проведи исследование и сохрани отчёт.
skill: sample_scenario
artifact_type: .md
processor: text
metric_groups:
  - quality
checks:
  - kind: text
    metric: Следование инструкции
    text: |-
      Отчёт содержит выводы и ссылки на источники.
```

`metric_groups` перечисляет только нужные группы. Группа сначала ищется в `checks/metrics/<scenario>/<group>.yaml`, затем в `checks/metrics/common/<artifact_type>/<group>.yaml`. Автоматического подключения всех проверок по типу файла нет.

## YAML-метрики и функциональные обработчики

Пример `checks/metrics/sample_scenario/quality.yaml`:

```yaml
id: quality
processor: text
phase: universal
checks:
  - kind: functional
    metric: Качество исследования
    handler: sample_domain/sample_check
    artifacts: [final]
    options:
      min_sources: 3
    text: Проверка источников

  - kind: text
    metric: Качество исследования
    text: |-
      Есть ли выводы, ограничения и ссылки на источники?
```

- `handler` — точный путь относительно `checks/functional/` без `.py`.
- `processor` — имя каталога с `processor.py`; на уровне группы служит значением по умолчанию для её проверок.
- `options` — произвольная YAML-структура, доступная как `check.options`.
- `phase: common` помещает группу перед индивидуальными `checks` кейса, `phase: universal` — после них.
- Полные пути модулей позволяют иметь одинаковые имена файлов в разных доменах. Сокращённый handler по имени файла допустим только при отсутствии дублей.
- Глобального `_HANDLERS` нет; новый модуль регистрировать не нужно.

Интерфейс проверки:

```python
def run(check, artifacts, photos, user_phrase=None, context=None):
    ...
```

Параметр `context` необязателен. Обработчики с четырьмя аргументами также поддерживаются.

## Процессоры артефактов

Процессор находится в `checks/functional/<domain>/processor.py`:

```python
class Processor:
    extensions = {".custom"}

    def extract_text(self, path: str) -> str:
        ...

    def render(self, path: str, output_dir) -> list[str]:
        ...
```

Фабрика сначала использует явный `processor` из YAML. Если он не задан, ищет процессор по расширению среди доступных `processor.py`. Для нового формата достаточно добавить новый домен с процессором; менять `evaluate.py` или judge не требуется.

Встроенные процессоры:

- `pptx` — PPTX;
- `office` — DOCX/XLSX/PDF;
- `text` — Markdown/TXT/JSON/CSV/HTML/SVG/log.

`context.extract_text(path, check.processor)` вызывает нужный процессор. Общие проверки в `text/` именно поэтому работают и с Office, и с текстовыми файлами без импорта `python-pptx`. Специализированные проверки и helpers остаются в `pptx/`.

Корневой `checks/functional/_helpers.py` содержит только универсальные `ok`/`unavailable` и построение XLSX-workbook. PPTX-helpers находятся в `checks/functional/pptx/_helpers.py`.

## Вложения и артефакты

```yaml
attachments:
  - name: template
    path: assets/template.pptx

artifacts:
  - name: extracted
    type: .md
    stage: intermediate
    processor: text
    path: extracted.md
    required: true
  - name: deck
    type: .pptx
    stage: final
    processor: pptx
    path: result.pptx
    required: true

checks:
  - kind: functional
    handler: sample_domain/sample_check
    artifacts: [template, extracted]
    text: Проверка промежуточного результата
```

- Пути вложений считаются от каталога сценария и передаются только с исходным `user_phrase`, не с «Продолжить».
- `stage` — `final` или `intermediate`; `type` — расширение; `path` — имя или glob относительно `work_dir`; `processor` переопределяет обработчик формата.
- `artifacts` проверки принимает `source`/`attachments`, `intermediate`, `final` или имена артефактов. По умолчанию — `final`.
- Промежуточный файл копируется при каждом подходящем событии в `intermediate_artifacts/`; версии и одинаковые basename не перезаписывают друг друга.
- `origin_path` сохраняет исходный путь, `event_index` — номер события.
- Каждый обязательный финальный артефакт проверяется отдельно. Пока все они не найдены, harness отправляет до семи «Продолжить».
- Отсутствующий обязательный артефакт даёт FAILED. Пересекающиеся описания, из-за которых один файл соответствует нескольким именам, отклоняются с понятной ошибкой.
- Для legacy-кейса с одним `artifact_type` и без `artifacts` создаётся одно финальное описание.
- `--validate` принимает существующий файл, соответствующий одному финальному описанию. При нескольких финалах проверяется только переданный файл; остальные обязательные будут отмечены отсутствующими.

`context` предоставляет `select(selectors)`, `paths(selectors)`, `photos_for(paths)`, `processor_for(...)` и `extract_text(...)`.

## Результаты

Полный прогон:

```text
runs/<yyyy-mm-dd_hh-mm>_<opencode_model>/
  run_<n>/<case_id>/
    work_dir/
    intermediate_artifacts/
    render_artifact/
    raw.log
  results.xlsx
```

Например: `runs/2026-10-15_15-40_deepseek_v4.1/run_1/GD-001/`. Provider из имени модели удаляется, недопустимые символы заменяются на `_`.

Валидация использует `runs/<yyyy-mm-dd_hh-mm>/run_1/example/result.xlsx`, поскольку OpenCode в ней не участвует. Ошибки judge помечаются `ERROR` и не считаются вердиктом по артефакту.

## Offline-проверки

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3.12 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3.12 -m example.run_test_agent --help
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3.12 -m example.run_test_agent --list-scenarios
```

Тесты покрывают независимое добавление сценария и процессора, существующие 447 проверок, вложения, промежуточные версии, несколько финальных артефактов, CLI и имя каталога модели. OpenCode, реальная обработка вложений через desktop и VLLM не запускаются.
