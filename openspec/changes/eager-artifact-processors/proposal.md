## Why

Processors должны полностью подготавливать ArtifactContext до checks, а не извлекать атрибуты по требованию отдельных проверок. Старый `judge/extract.py` и ленивая материализация скрывали форматную логику вне конкретных processors. Параметр `stage` одновременно управлял финальностью и snapshot-историей, хотя текущим сценариям нужна только явная маркировка итогового результата.

## What Changes

- **BREAKING**: `stage` заменён на `is_final_result: true`; вспомогательные файлы не требуют флага.
- Автоматические intermediate-snapshot’ы удалены из базового поведения.
- `ArtifactContext` целиком проходит `process_context()` до functional/LLM checks.
- Каждый processor сразу извлекает все свои атрибуты в исходный Artifact.
- PPTX/DOCX/XLSX владеют своим извлечением; общий модуль содержит только XML helpers.
- `judge/extract.py`, compatibility-wrapper’ы и lazy `materialize()` удалены.
- Ошибки извлечения сохраняются в `Artifact.processing_errors` по атрибутам.

## Capabilities

### New Capabilities

- нет.

### Modified Capabilities

- `lib-ai-quality-harness`: processors работают eager до checks; роли артефактов и готовность кейса используют `is_final_result`; автоматические промежуточные версии удалены.

## Impact

- `example/models.py`, `example/artifacts.py`, `example/evaluate.py`.
- `example/checks/processors/`, functional и judge runners.
- YAML кейсов, тесты, README и ADD_TEST_CASES.
- Клиентские изменения вне этого контура не затрагиваются.
