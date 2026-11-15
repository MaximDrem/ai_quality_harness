# Добавление сценариев, кейсов и проверок

Документ описывает текущие правила `refactoring/example`. Основной запуск и устройство клиента описаны в `README.md` и `client/README.md`.

## 1. Из чего состоит сценарий

Для сценария `presentation_style`:

```text
example/
  data/presentation_style/
    STYLE-001.yaml
    assets/
      source.pptx

  checks/
    metrics/presentation_style/
      extracted_elements.yaml

    functional/presentation_style/
      extracted_elements.py
```

- `data/<scenario>/` — только кейсы и их входные файлы.
- `checks/metrics/<scenario>/` — группы проверок в YAML.
- `checks/functional/<domain>/` — необязательные Python-проверки и процессоры.
- Имя `domain` не обязано совпадать со `scenario`, но для собственных проверок сценария такое совпадение упрощает навигацию.
- Сценарий обнаруживается автоматически по наличию YAML непосредственно в `data/<scenario>/`. Регистрировать его в Python не нужно.

Запуск:

```bash
PYTHONPATH=. python3.12 -m example.run_test_agent \
  --scenario presentation_style --id STYLE-001 --runs 1
```

## 2. YAML тест-кейса

Минимальный кейс с одним финальным файлом:

```yaml
id: DR-001
user_phrase: |-
  Проведи исследование и сохрани отчёт.
skill: deep_research
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

Поля кейса:

| Поле | Назначение |
|---|---|
| `id` | Уникальный ID внутри сценария; используется в `--id` и имени каталога результата. |
| `user_phrase` | Первое сообщение агенту. |
| `skill` | Название скила; если не задано, используется имя сценария. |
| `artifact_type` | Тип единственного финала для простого кейса, например `.pptx`. Если задан `artifacts`, может быть взят из первого final-описания. |
| `processor` | Процессор по умолчанию для артефактов и индивидуальных проверок кейса. Для разнотипных артефактов лучше задавать процессор отдельно каждому. |
| `metric_groups` | Имена YAML-групп без `.yaml`. Ничего дополнительно по типу файла автоматически не подключается. |
| `attachments` | Входные файлы агента. |
| `artifacts` | Описания промежуточных и финальных результатов. |
| `checks` | Индивидуальные проверки кейса. |

Для одного финала можно использовать только `artifact_type`. Для нескольких результатов нужен `artifacts`.

## 3. YAML группы метрик

Файл `checks/metrics/presentation_style/extracted_elements.yaml`:

```yaml
id: extracted_elements
phase: universal
checks:
  - kind: functional
    metric: Все ли элементы исходной презентации извлечены
    handler: presentation_style/extracted_elements
    artifacts: [source, extracted_slides]
    options:
      json_code_fence: json
    text: Сравнение объектов исходной PPTX с JSON в промежуточном Markdown
```

Правила:

- Кейс подключает группу через `metric_groups: [extracted_elements]`.
- Сначала ищется `checks/metrics/<scenario>/<group>.yaml`, затем `checks/metrics/common/<artifact_type>/<group>.yaml`.
- `phase: common` ставит группу перед индивидуальными проверками кейса; `phase: universal` — после них.
- `processor` на уровне группы необязателен и становится значением по умолчанию для проверок группы.
- `kind: text` и `kind: vlm` используют существующий LLM-judge; `handler` им не нужен.
- `kind: functional` требует Python-обработчик.
- `options` передаётся в обработчик как `check.options`.
- `user_phrase: false` запрещает передавать исходный запрос в проверку; по умолчанию он передаётся.
- `single_slide: true` включает существующий послайдовый режим judge, если он применим.

## 4. Python функциональной проверки

Файл `checks/functional/presentation_style/extracted_elements.py` должен экспортировать:

```python
def run(check, artifacts, photos, user_phrase=None, context=None):
    ...
```

Возвращается `CheckResult`, обычно через универсальные helpers:

```python
from example.checks.functional._helpers import ok, unavailable
```

Внутри доступны:

- `check.options` — параметры из YAML;
- `artifacts` — пути, выбранные полем `artifacts` проверки;
- `photos` — изображения этих артефактов, если их удалось получить;
- `context.select([...])` — объекты `CollectedArtifact` с именем, ролью и версией;
- `context.paths([...])` — только пути;
- `context.extract_text(path)` — текст через процессор конкретного артефакта;
- `item.origin_path` — исходный путь промежуточного файла до snapshot;
- `item.event_index` — событие, на котором сохранена версия.

Не нужно изменять `checks/functional/__init__.py`: `handler` загружается по полному пути. Одинаковые basename в разных каталогах допустимы.

Существующие обработчики можно переиспользовать по полному пути, например:

```yaml
handler: text/repeated_words
```

или:

```yaml
handler: pptx/instruction/slide_count
options:
  condition: 10
```

## 5. Переиспользование процессора артефакта

Если тип файла уже поддерживается, новый процессор не нужен. Достаточно указать существующий:

```yaml
artifacts:
  - name: deck
    stage: final
    type: .pptx
    processor: pptx
    path: new_presentation/result.pptx
```

Доступные встроенные процессоры:

- `pptx` — PPTX;
- `office` — DOCX/XLSX/PDF;
- `text` — Markdown/TXT/JSON/CSV/HTML/SVG/log.

Если `processor` не указан, он выбирается по расширению. Явное указание предпочтительнее при нескольких типах файлов и переопределённых форматах.

Новый процессор нужен только для нового способа чтения или рендера. Он добавляется как `checks/functional/<domain>/processor.py`:

```python
class Processor:
    extensions = {".custom"}

    def extract_text(self, path: str) -> str:
        ...

    def render(self, path: str, output_dir) -> list[str]:
        ...
```

Процессор не определяет состав проверок. Он только читает и рендерит файл; проверки выбираются YAML.

## 6. Описание артефактов

```yaml
attachments:
  - name: source
    path: assets/source.pptx

artifacts:
  - name: extracted_slides
    stage: intermediate
    type: .md
    processor: text
    path: "*-ref/*.md"
    required: true

  - name: deck
    stage: final
    type: .pptx
    processor: pptx
    path: new_presentation/result.pptx
    required: true
```

Правила:

- `name` уникально внутри кейса и используется в `artifacts` проверки.
- `stage` — `final` или `intermediate`.
- `type` — расширение с точкой.
- `processor` — обработчик этого конкретного файла.
- `path` — путь или glob относительно `work_dir` агента.
- `required: true` означает, что отсутствие файла даёт FAILED.
- Пути вложений считаются от `data/<scenario>/`, а не от `work_dir`.

Селекторы проверки:

- `source` или `attachments` — исходные вложения;
- `intermediate` — все snapshot’ы промежуточных файлов;
- `final` — финальные файлы;
- конкретное `name`, например `source`, `extracted_slides`, `deck`.

По умолчанию проверка получает `final`.

## 7. Как ищутся файлы в `work_dir`

Промежуточные файлы собираются во время событий агента:

1. Из путей завершённых `write`/`edit`.
2. Из tool output и текстовых сообщений.
3. Из рекурсивного сканирования `work_dir` по промежуточным glob’ам — это нужно и для shell-команд без явного пути в output.

Каждая изменившаяся версия копируется в `intermediate_artifacts/`. Повторное чтение неизменного содержимого не создаёт дубль. Проверка получает snapshot, а исходный путь доступен в `origin_path`.

Финальные файлы собираются из tool input/output, найденных клиентом артефактов и рекурсивного сканирования `work_dir`. Поэтому вложенная структура поддерживается:

```text
work_dir/new_presentation/result.pptx
```

Её можно описать как `new_presentation/result.pptx` или `new_presentation/*.pptx`.

Если `path` не содержит `/`, glob применяется к basename на любом уровне вложенности. Например, `*.pptx` ищет все PPTX рекурсивно. Если `/` есть, glob сравнивается с относительным путём.

Один файл может соответствовать нескольким файлам одного `name`, но не нескольким разным `name`: пересекающиеся описания отклоняются с ошибкой.

Финальная фраза «Ваша презентация готова» сама по себе не обязательна: файл внутри `work_dir` будет найден сканированием. Надёжный вариант — создать файл внутри `work_dir` и вернуть его путь без пробелов.

## 8. Пример: JSON слайдов в `*-ref/*.md`

Сценарий создаёт:

```text
work_dir/
  my_presentation-ref/
    extracted.md
  my_presentation/
    result.pptx
```

Кейс:

```yaml
id: STYLE-001
user_phrase: |-
  Скопируй стиль исходной презентации в новую презентацию.
skill: presentation_style
artifact_type: .pptx
metric_groups:
  - extracted_elements

attachments:
  - name: source
    path: assets/source.pptx

artifacts:
  - name: extracted_slides
    stage: intermediate
    type: .md
    processor: text
    path: "*-ref/*.md"
    required: true

  - name: deck
    stage: final
    type: .pptx
    processor: pptx
    path: "my_presentation/*.pptx"
    required: true
```

Метрика приведена в разделе 3. Проверка должна:

1. Получить исходную PPTX через `context.select(["source"])`.
2. Получить версии Markdown через `context.select(["extracted_slides"])`.
3. Выбрать последнюю версию или проверить все версии.
4. Прочитать Markdown через `context.extract_text(extracted.path)`.
5. Самостоятельно извлечь JSON из code fences и выполнить `json.loads`.
6. Сравнить JSON с объектами исходной PPTX.
7. Вернуть `ok(...)` или `unavailable(...)`.

Каркас без реализации конкретного сравнения:

```python
def run(check, artifacts, photos, user_phrase=None, context=None):
    source = context.select(["source"])[0]
    versions = context.select(["extracted_slides"])
    if not versions:
        return unavailable(check, "Нет extracted_slides", artifacts, photos, user_phrase)

    extracted = versions[-1]
    markdown = context.extract_text(extracted.path)

    # Здесь обработчик извлекает JSON из markdown,
    # читает source.path и сравнивает элементы презентации.
    ...
```

В этом примере у группы и проверки не задан общий `processor`: у source он `pptx` по расширению, у Markdown явно `text`. Поэтому `context.extract_text(extracted.path)` вызовет правильный процессор. Не нужно передавать `check.processor="pptx"` для разнотипных входов.

Встроенный `text`-процессор только читает Markdown. Извлечение JSON и сравнение элементов — ответственность отдельной функциональной проверки.

## 9. Текущие ограничения и проблемные случаи

### Широкий glob может принять исходную PPTX за финальную

Клиент копирует вложения в:

```text
work_dir/uploads/<session>/<message>/
```

Рекурсивный поиск финалов сейчас не исключает `uploads/`. Поэтому при загруженной исходной PPTX широкие `*.pptx` и `*/*.pptx` могут ошибочно засчитать её как результат и преждевременно прекратить «Продолжить».

До отдельной доработки фильтрации нужно задавать специфичную выходную папку, например:

```yaml
path: "my_presentation/*.pptx"
```

Если имя выходной папки заранее неизвестно и она может находиться на том же уровне, что и `uploads/`, текущего декларативного механизма недостаточно. Потребуется добавить `exclude` или исключить `uploads/` из финальных кандидатов в коде.

### Файл вне `work_dir`

Рекурсивное сканирование работает только внутри `work_dir`. Путь из финального текстового сообщения к внешнему файлу сейчас не является надёжным способом регистрации финала. Агент должен сохранять результат внутри `work_dir`; пути с пробелами также лучше избегать.

### Несколько финалов в `--validate`

`--validate` принимает один файл. Он должен соответствовать одному final-описанию; остальные обязательные финалы будут отмечены отсутствующими. Для комплексной проверки нескольких готовых файлов нужен отдельный режим или расширение CLI.

### Проверки клонирования стиля

Архитектура source/intermediate/final уже поддерживает описанный сценарий, но конкретные алгоритмы извлечения JSON и сравнения шрифтов, цветов, фигур и других элементов пока не реализованы.

## 10. Минимальная проверка нового сценария

Без OpenCode и VLLM можно проверить загрузку:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3.12 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3.12 -m example.run_test_agent --list-scenarios
```

Перед полным прогоном проверить:

- сценарий и ID видны;
- все `metric_groups` существуют;
- каждый `handler` и `processor` загружается;
- относительные вложения существуют;
- промежуточный glob соответствует фактической структуре;
- финальный glob не захватывает `uploads/` и исходные файлы;
- имена артефактов уникальны и проверки используют правильные селекторы.
