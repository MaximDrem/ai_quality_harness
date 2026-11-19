## Why

Первая реализация сценария клонирования оформила M3 и M5 как одиночные агрегированные handler’ы. Это скрывает конкретную причину провала и не позволяет метрике частично учитываться через `pass/all`.

## What Changes

- M1 становится набором из двух независимых проверок: формат заголовка `STYLE.md` и совпадение имени шаблона с вложением.
- M3 разбивается на шесть независимых проверок: размер слайда, master/theme, набор layouts, использование layouts, шрифты текста и цвета текста.
- M5 разбивается на три независимые PPTX-проверки: текст слайдов/заметок, метаданные и видимые ссылки.
- Пакетные проверки шаблона переносятся из общего `path/`-домена в PPTX-domain.
- Убирается агрегированный `template_fidelity` handler.

## Capabilities

### New Capabilities

- нет.

### Modified Capabilities

- `presentation-clone`: метрики M1/M3/M5 становятся наборами независимых проверок с раздельной фиксацией причин.

## Impact

- `example/checks/functional/pptx/raw_structure/`
- `example/checks/functional/text/`
- `example/metrics/presentation_clone/`
- `tests/test_presentation_clone.py`
- `README.md`
- Offline-тесты и OpenSpec strict; VLLM и полный прогон агента не требуются.
