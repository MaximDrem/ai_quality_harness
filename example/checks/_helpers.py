"""Универсальные результаты проверок Artifact и построение XLSX.

Не зависит от формата файла: работает с path, text, raw_structure,
rendered_photos и виртуальным final_message одинаково.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from ..models import Artifact, Check, CheckResult, RunResult


def source_str(artifacts: Sequence[Artifact], user_phrase: Optional[str]) -> str:
    photos = sum(len(artifact.rendered_photos) for artifact in artifacts)
    value = f"артефактов: {len(artifacts)}, фото: {photos}"
    return value + f", user_prompt: {'да' if user_phrase else 'нет'}"


def _result(
    check: Check,
    success: bool,
    reason: str,
    artifacts: Sequence[Artifact],
    user_phrase: Optional[str],
) -> CheckResult:
    primary = artifacts[-1] if artifacts else None
    return CheckResult(
        check=check,
        success=success,
        reason=reason,
        mode=check.mode,
        source=source_str(artifacts, user_phrase),
        artifact_path=primary.path if primary else None,
        input_paths=[artifact.path for artifact in artifacts if artifact.path],
    )


def _phrase(photos_or_phrase, user_phrase=None):
    return user_phrase if isinstance(photos_or_phrase, list) else photos_or_phrase


def ok(check: Check, reason: str, artifacts: Sequence[Artifact], photos_or_phrase=None, user_phrase=None) -> CheckResult:
    return _result(check, True, reason, artifacts, _phrase(photos_or_phrase, user_phrase))


def unavailable(
    check: Check,
    reason: str,
    artifacts: Sequence[Artifact],
    photos_or_phrase=None,
    user_phrase=None,
) -> CheckResult:
    return _result(check, False, reason, artifacts, _phrase(photos_or_phrase, user_phrase))


def _token_str(result: RunResult) -> str:
    tokens = result.tokens or {}
    return f"in={tokens.get('input', 0)} out={tokens.get('output', 0)} total={tokens.get('total', 0)}"


def build_results_workbook(results: List[RunResult]) -> Workbook:
    headers = [
        "Кейс", "Прогон", "№", "Проверка", "Режим", "Тип", "Метрика",
        "Результат", "Обоснование", "Статус", "Время_агента_сек", "Токены",
        "Артефакт", "Атрибут",
    ]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Проверки"
    sheet.append(headers)
    header_fill = PatternFill("solid", fgColor="D9E2F3")
    for column in range(1, len(headers) + 1):
        sheet.cell(row=1, column=column).fill = header_fill
    for result in results:
        base = [result.test_case.id, result.run]
        routing_reason = (
            f"Ожидался skill={result.test_case.skill!r}, "
            f"агент выбрал {result.actual_skill or '—'!r}"
        )
        if result.routing_applicable:
            sheet.append(base + [
                "", "routing", result.test_case.skill, "routing", "routing",
                "PASSED" if result.routing_success else "FAILED",
                routing_reason, "", round(result.agent_time_sec, 3), _token_str(result), "", "",
            ])
        if result.dropped:
            sheet.append(base + [
                "", "", "", "", "", "DROP",
                result.drop_reason or "агент задал уточняющий вопрос",
                "Вопрос агента", round(result.agent_time_sec, 3), _token_str(result), "", "",
            ])
            continue
        for index, check_result in enumerate(result.check_results, start=1):
            sheet.append(base + [
                index,
                check_result.check.text,
                check_result.mode,
                check_result.check.kind,
                check_result.check.metric or "",
                "ERROR" if check_result.error else ("PASSED" if check_result.success else "FAILED"),
                check_result.reason,
                "ОШИБКА СУДЬИ" if check_result.error else "",
                round(result.agent_time_sec, 3),
                _token_str(result),
                check_result.artifact_path or "",
                check_result.input_attribute,
            ])
        sheet.append(base + [
            "", "agent_time", "", "runtime", "agent_time", "INFO",
            f"Время выполнения: {result.agent_time_sec:.3f} сек.",
            "", round(result.agent_time_sec, 3), _token_str(result), "", "",
        ])
        sheet.append(base + [
            "", "tools", "", "runtime", "tools", "INFO",
            "Вызванные тулы: " + (", ".join(result.tools) or "—"),
            "", round(result.agent_time_sec, 3), _token_str(result), "", "",
        ])
        if result.context_limit or result.context_tokens:
            sheet.append(base + [
                "", "context", "", "runtime", "context", "INFO",
                f"Контекст: {result.context_tokens}/{result.context_limit} токенов "
                f"({result.context_percent:.1f}%)",
                "", round(result.agent_time_sec, 3), _token_str(result), "", "",
            ])
    return workbook
