## 1. YAML и артефакты

- [x] 1.1 Заменить stage на is_final_result.
- [x] 1.2 Сохранить artifact_type и final_message как неявные финалы.
- [x] 1.3 Собирать вспомогательные файлы без snapshot-версий.
- [x] 1.4 Мигрировать YAML presentation_clone.

## 2. Eager processors

- [x] 2.1 Добавить process_context для всего ArtifactContext.
- [x] 2.2 Извлекать все атрибуты каждого processor до checks.
- [x] 2.3 Сохранять ошибки по атрибутам в Artifact.
- [x] 2.4 Подготовить serialized_raw_structure заранее.

## 3. Форматное извлечение

- [x] 3.1 Перенести PPTX-извлечение в processor_pptx.
- [x] 3.2 Перенести DOCX- и XLSX-извлечение в их processors.
- [x] 3.3 Оставить общие только XML zip helpers.
- [x] 3.4 Удалить judge/extract.py и центральный suffix-диспетчер.

## 4. Checks и тесты

- [x] 4.1 Удалить materialize из functional и judge runners.
- [x] 4.2 Обновить тесты на process_context.
- [x] 4.3 Обновить README и ADD_TEST_CASES.
- [x] 4.4 Выполнить доступные статические и offline-проверки.
- [x] 4.5 Провалидировать OpenSpec change и существующие specs.
