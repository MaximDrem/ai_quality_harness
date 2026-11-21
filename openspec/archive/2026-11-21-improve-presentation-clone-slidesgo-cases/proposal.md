## Why

Сценарий `presentation_clone` не проверял индивидуальные требования к числу слайдов, структуре, фактам и языку. Проверка формата `STYLE.md` ошибочно требовала PPTX, хотя получает только Markdown, а запрещённые следы шаблона были общими для разных презентаций. Также требовалось обновить AI Technology и добавить кейсы для Remote Working.

## What Changes

- Обновлён шаблон AI Technology и добавлен Remote Working.
- Настроены кейсы `US01-01-01…06`: три на AI Technology и три на Remote Working.
- Для этих кейсов добавлены функциональная проверка числа слайдов и индивидуальные LLM-проверки следования инструкции, содержания и языка.
- Проверки чистоты M5 перенесены из общей группы в YAML кейсов с отдельными `terms/patterns` для AI Technology и Remote Working.
- Проверка M1 разделена на самостоятельную проверку формата заголовка `STYLE.md` и проверку соответствия имени приложенному шаблону.
- Добавлена переиспользуемая функциональная проверка, запрещающая медиафайлы, отсутствующие в исходном шаблоне.

## Capabilities

### Modified Capabilities

- `presentation-clone`: кейсы AI Technology и Remote Working, индивидуальные проверки задания и чистоты, независимые проверки M1 и переиспользуемый контроль происхождения изображений.

## Impact

- Кейсы и вложения: `example/data/presentation_clone/`.
- Группы метрик: `example/metrics/presentation_clone/`.
- Функциональные проверки: `example/checks/functional/text/`, `example/checks/functional/pptx/raw_structure/`.
- Тесты и документация: `tests/test_presentation_clone.py`, `README.md`.
- Полный агентский прогон и VLLM не требуются; проверка выполняется offline.
