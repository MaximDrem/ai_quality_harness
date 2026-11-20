## Context

ArtifactContext является центральным состоянием артефактов, извлечённых данных и результатов. Ранее checks сами запрашивали отдельные атрибуты, а OfficeProcessor обращался к parser в judge. Это смешивало orchestration, форматное извлечение и судейство.

## Goals / Non-Goals

**Goals:**

- полный processor pipeline до первого check;
- динамический выбор processor по YAML override или расширению;
- форматная логика только в конкретном processor;
- вспомогательные и финальные файлы обрабатываются одинаково полно;
- только финальность влияет на готовность кейса и «Продолжить».

**Non-Goals:**

- сохранять историю перезаписей без отдельного требования;
- менять scoring, prompts и сами проверки;
- запускать реальный агентский прогон как smoke-test.

## Decisions

1. `process_context(context)` вызывается в evaluate после установки render_dir и до missing/functional/judge checks.
2. `BaseProcessor.process(artifact)` обходит text/raw_structure/rendered_photos и заполняет serialized_raw_structure.
3. Ошибка одного атрибута не отменяет остальные: она записывается в processing_errors, а check получает недоступность только требуемого входа.
4. `ArtifactContext.processed` не позволяет checks незаметно работать с необработанным контекстом.
5. `is_final_result` по умолчанию false; в YAML его указывают только при true. Старый одиночный artifact_type остаётся сокращением одного финала.
6. Роль supporting используется для вспомогательных файлов. Их актуальные файлы собираются в конце работы агента, без snapshot-истории.
7. `final_message` не требует файлового processor; tool_calls приходят из событий.

## Risks / Trade-offs

- [Eager-рендер увеличивает время обработки] → Это целевое требование: все данные готовы до checks.
- [Удаление snapshots исключает проверки истории] → Текущие сценарии используют актуальное состояние; история должна вводиться отдельным keep_versions-контрактом.
- [YAML несовместим со stage] → Загрузчик явно отклоняет stage, кейсы мигрированы.
- [Ошибка извлечения может быть скрыта] → Она сохраняется в Artifact и возвращается check как понятная недоступность.

## Migration Plan

1. Заменить модели и загрузчик YAML.
2. Упростить collector и убрать snapshots.
3. Разнести Office extraction по processors и добавить process_context.
4. Удалить lazy-вызовы и judge/extract.py.
5. Мигрировать YAML, тесты и документацию.
6. Проверить статические инварианты и доступные offline-команды.
