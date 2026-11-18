# Добавление тест-кейса и проверок

## 1. Структура

```text
example/
  data/<scenario>/<case>.yaml
  metrics/<scenario>/<group>.yaml
  checks/functional/<attribute>/<check>.py
```

Новый сценарий определяется автоматически по YAML в `data/<scenario>/`. Регистрация в Python не нужна.

## 2. Кейс

```yaml
id: SAMPLE-001
user_phrase: |-
  Создай презентацию по заданию.
skill: presentation
artifact_type: .pptx
metric_groups:
  - sample_group
```

`artifact_type` автоматически выбирает процессор по расширению. Поле `processor` нужно только для нестандартного override.

Для нескольких результатов используйте `artifacts` вместо одного `artifact_type`. Кейс обязан объявить файловый финал или `final_message`; иначе загрузка завершится ошибкой до запуска агента:

```yaml
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

`path` — путь или glob относительно `work_dir`; для файлового артефакта обязателен `type` или `path`. Пути из свободного текста распознаются только для типов, явно объявленных в YAML кейса. Вложения:

```yaml
attachments:
  - name: source
    path: assets/source.pptx
```

Путь вложения считается от каталога сценария.

## 3. Группа метрик

`metrics/<scenario>/sample_group.yaml`:

```yaml
id: sample_group
phase: universal
weight: 1
checks:
  - kind: function
    metric: Пример метрики
    handler: pptx/raw_structure/sample_check
    artifacts: [deck]
    options:
      threshold: 10
    text: Проверка структуры результата
```

- `metric_groups` подключает только перечисленные группы.
- `weight` группы наследуется проверками; без него вес равен `1`.
- `phase: common` ставит группу перед индивидуальными проверками, `universal` — после.
- Типов два: `function` для Python и `llm` для judge.
- Для `llm` обязателен `input: text | raw_structure | rendered_photos | tool_call`.

Отдельная проверка кейса тоже может иметь вес:

```yaml
checks:
  - kind: function
    metric: Отдельная метрика
    weight: 0.5
    handler: text/sample_check
    text: Отдельная проверка
```

Конфликтующие веса одной метрики запрещены.

## 4. Функциональная проверка

`checks/functional/pptx/raw_structure/sample_check.py`:

```python
from example.checks._helpers import ok, unavailable


def run(check, artifacts, user_phrase=None, context=None):
    value = check.options.get("threshold")
    if artifacts[0].raw_structure:
        return ok(check, "Структура получена", artifacts, user_phrase)
    return unavailable(check, "Структура отсутствует", artifacts, user_phrase)
```

Каталог handler определяет нужный атрибут:

| Каталог | Атрибут |
|---|---|
| `path/` | `artifact.path` |
| `text/` | `artifact.text` |
| `raw_structure/` | `artifact.raw_structure` |
| `rendered_photos/` | `artifact.rendered_photos` |
| `tool_calls/` | `artifact.tool_calls` |

Если нужны дополнительные атрибуты, в модуле укажите:

```python
REQUIRES = {"raw_structure", "rendered_photos"}
```

Результат автоматически добавляется в `artifact.results`.

Функциональная проверка инструментов:

```yaml
- kind: function
  handler: tool_calls/tool_called
  options: {tool: write}
  text: Файл записан
```

Проверка той же трассы через judge:

```yaml
- kind: llm
  input: tool_call
  text: Файл сохранён успешно
```

Для judge `input` обязателен: `text | raw_structure | rendered_photos | tool_call`.

## 5. Промпт judge

По умолчанию используется `checks/llm/presentation/`. Для своего сценария создайте `checks/llm/<scenario>/__init__.py` и экспортируйте:

```python
SYSTEM_PROMPT = "..."
build_text_prompt = ...
build_vision_prompt = ...
JudgeResult = ...
```

`build_text_prompt(agent_text, user_instruction, check_instruction)` и `build_vision_prompt(user_instruction, check_instruction, number_of_images)` вызываются при `--scenario <scenario>`. `JudgeResult` — Pydantic-модель structured output.

## 6. Процессоры

Существующий тип переиспользуется автоматически по расширению. Новый формат требует только `checks/processors/processor_<name>.py`:

```python
class Processor:
    extensions = {".custom"}

    def process(self, artifact, attributes):
        for attribute in attributes:
            if attribute == "text":
                artifact.text = ...
            elif attribute == "raw_structure":
                artifact.raw_structure = ...
            elif attribute == "rendered_photos":
                artifact.rendered_photos = ...
            artifact.loaded_attributes.add(attribute)
```

Процессор заполняет атрибуты, но не выбирает проверки.

## 7. Финальный ответ без файла

`final_message` доступен как виртуальный артефакт с текстом финального ответа:

```yaml
artifacts:
  - name: final_message
    stage: final
    kind: message
    required: true

checks:
  - kind: llm
  input: text
    artifacts: [final_message]
    metric: Качество ответа
    text: Ответ полный и соответствует заданию
```

Файл в этом случае не требуется: пути из ответа не извлекаются, поиск по `work_dir` не выполняется, а завершённый ответ прекращает «Продолжить».

## 8. Выбор файлового финала

Сначала проверяется путь из завершённого финального сообщения. Если подходящего пути нет, выполняется поиск по YAML glob внутри `work_dir`; затем отправляется «Продолжить». `uploads/` и исходные attachments не участвуют в выборе финала.

## 9. Результаты

Полный прогон создаёт `raw.log`, человекочитаемый `agent.log`, XLSX и `one_number_result.txt`. Сначала вычисляется `S_case`, затем `S_scenario` как среднее валидных `S_case`. Routing и время отображаются отдельно и не входят в `S`.

Offline-проверка:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3.12 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3.12 -m example.run_test_agent --list-scenarios
```
