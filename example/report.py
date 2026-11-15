"""Итоговый отчёт results.xlsx по всем кейсам и их проверкам."""
from __future__ import annotations

from pathlib import Path
from typing import List

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from .models import RunResult

_HEADERS = [
    "Кейс", "Прогон", "№", "Проверка", "Режим", "Тип", "Метрика",
    "Результат", "Обоснование", "Статус", "Время_агента_сек", "Токены",
]


def _token_str(rr: RunResult) -> str:
    t = rr.tokens or {}
    return f"in={t.get('input', 0)} out={t.get('output', 0)} total={t.get('total', 0)}"


def write_results_xlsx(results: List[RunResult], out_path: Path) -> str:
    """Собрать единый xlsx со всеми кейсами и их проверками."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Проверки"

    ws.append(_HEADERS)
    header_fill = PatternFill("solid", fgColor="D9E2F3")
    for c in range(1, len(_HEADERS) + 1):
        ws.cell(row=1, column=c).fill = header_fill

    for rr in results:
        if rr.dropped:
            # Уточняющий вопрос — кейс дропнут.
            ws.append([
                rr.test_case.id,
                rr.run,
                "", "", "", "", "",
                "DROP",
                rr.drop_reason or "агент задал уточняющий вопрос",
                "Вопрос агента",
                round(rr.agent_time_sec, 3),
                _token_str(rr),
            ])
            continue
        for i, cr in enumerate(rr.check_results, start=1):
            ws.append([
                rr.test_case.id,
                rr.run,
                i,
                cr.check.text,
                cr.mode,
                cr.check.kind,
                cr.check.metric or "",
                # Ошибка судьи — не вердикт по артефакту.
                "ERROR" if cr.error else ("PASSED" if cr.success else "FAILED"),
                cr.reason,
                "ОШИБКА СУДЬИ" if cr.error else "",
                round(rr.agent_time_sec, 3),
                _token_str(rr),
            ])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return str(out_path)