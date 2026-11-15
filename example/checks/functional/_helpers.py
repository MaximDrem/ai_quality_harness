from __future__ import annotations

from typing import List, Optional

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from ...models import Check, CheckResult, RunResult


def source_str(artifacts: List[str], photos: List[str], user_phrase: Optional[str]) -> str:
    value = f"артефактов: {len(artifacts)}, фото: {len(photos)}"
    return value + f", user_prompt: {'да' if user_phrase else 'нет'}"


def ok(
    check: Check,
    reason: str,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str],
) -> CheckResult:
    return CheckResult(
        check=check,
        success=True,
        reason=reason,
        mode=check.mode,
        source=source_str(artifacts, photos, user_phrase),
    )


def unavailable(
    check: Check,
    reason: str,
    artifacts: List[str],
    photos: List[str],
    user_phrase: Optional[str],
) -> CheckResult:
    return CheckResult(
        check=check,
        success=False,
        reason=reason,
        mode=check.mode,
        source=source_str(artifacts, photos, user_phrase),
    )


def _token_str(result: RunResult) -> str:
    tokens = result.tokens or {}
    return f"in={tokens.get('input', 0)} out={tokens.get('output', 0)} total={tokens.get('total', 0)}"


def build_results_workbook(results: List[RunResult]) -> Workbook:
    headers = [
        "Кейс", "Прогон", "№", "Проверка", "Режим", "Тип", "Метрика",
        "Результат", "Обоснование", "Статус", "Время_агента_сек", "Токены",
    ]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Проверки"
    sheet.append(headers)
    header_fill = PatternFill("solid", fgColor="D9E2F3")
    for column in range(1, len(headers) + 1):
        sheet.cell(row=1, column=column).fill = header_fill
    for result in results:
        if result.dropped:
            sheet.append([
                result.test_case.id,
                result.run,
                "", "", "", "", "",
                "DROP",
                result.drop_reason or "агент задал уточняющий вопрос",
                "Вопрос агента",
                round(result.agent_time_sec, 3),
                _token_str(result),
            ])
            continue
        for index, check_result in enumerate(result.check_results, start=1):
            sheet.append([
                result.test_case.id,
                result.run,
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
            ])
    return workbook
