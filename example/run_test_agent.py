"""Прогон тест-кейсов против headless-бэкенда GigaCat.

    PYTHONPATH=. python3 -m example.run_test_agent [--id GD-001] [--validate file.pptx]
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .agent.cookies import refresh_cookies
from .agent.session import run_case
from .cases import load_test_cases, makedirs
from .config import RUNS_DIR, SEMAPHORE_LIMIT, WORK_DIRS_DIR, load_dotenv
from .evaluate import case_for_validate, list_models, validate_artifact, with_universal_checks
from .models import RunResult
from .report import write_results_xlsx
from tqdm import tqdm


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--id", default=None, help="фильтр по id кейса (по умолчанию — все из data/*.yaml)")
    p.add_argument("--model", default=None, help="модель в формате provider/model, напр. gigachat/Qwen3.5-397b (env: GIGATOOL_MODEL)")
    p.add_argument("--list-models", action="store_true", help="показать доступные модели и выйти")
    p.add_argument("--validate", default=None,
                   help="проверить ГОТОВЫЙ артефакт (pptx/docx/xlsx) без запуска агента, напр. work_dirs/example/slides.pptx")
    p.add_argument("--case", default=None,
                   help="id тест-кейса, чей check_list использовать для --validate (по умолчанию presentation для .pptx)")
    p.add_argument("--checks", default=None,
                   help="для --validate: многострочный список text-проверок (по одной на строку), переопределяющий чек-лист кейса")
    p.add_argument("--runs", type=int, default=1, help="количество прогонов одного датасета")
    return p.parse_args(argv)


def print_metrics_summary(rr: RunResult) -> None:
    """Напечатать "метрика: passed/total" по всем проверкам кейса.

    Проверки без метрики группируются под "—", ошибки судьи не считаются
    и выводятся отдельной строкой.
    """
    # Метрика -> [пройдено, всего]; порядок входа сохраняем для вывода.
    agg: Dict[str, List[int]] = collections.OrderedDict()
    errors = 0
    for cr in rr.check_results:
        if cr.error:
            errors += 1
            continue
        key = cr.check.metric or "—"
        agg.setdefault(key, [0, 0])
        agg[key][1] += 1
        if cr.success:
            agg[key][0] += 1

    for metric, (passed, total) in agg.items():
        flag = "OK " if passed == total and total else "!! "
        print(f"    {flag}[{metric}] {passed}/{total}")
    if errors:
        print(f"    ?? [ОШИБКИ СУДЬИ] {errors} — в метрики не входят")


async def _validate_mode(args: argparse.Namespace, runs_root: Path) -> int:
    """Проверить готовый файл без агента: рендер + проверки + отчёт."""
    case = case_for_validate(args.validate, args.case, args.checks)
    print(f"[validate:{case.id}] файл={args.validate}")
    rr = await validate_artifact(case, args.validate, runs_root)
    result_xlsx = write_results_xlsx([rr], runs_root / "run_1" / case.id / "result.xlsx")
    rr.judge_xlsx = result_xlsx

    passed = [c for c in rr.check_results if c.success]
    errored = [c for c in rr.check_results if not c.success and c.error]
    failed = [c for c in rr.check_results if not c.success and not c.error]
    # Ошибки судьи — не вердикт по колоде, печатаем отдельно.
    print(f"[validate:{case.id}] прошло {len(passed)}/{len(rr.check_results) - len(errored)}"
          f" (ошибок судьи: {len(errored)}); результат: {result_xlsx}")
    for c in failed:
        print(f"  [FAIL] {c.check.text}\n         {c.reason}")
    for c in errored:
        print(f"  [ERROR] {c.check.text}\n          {c.reason}")
    for c in passed:
        print(f"  [PASS] {c.check.text}")
    print(f"[validate:{case.id}] метрики:")
    print_metrics_summary(rr)
    return 0 if not failed else 1


async def _full_run_mode(args: argparse.Namespace, runs_root: Path) -> int:
    """Прогнать тест-кейсы против агента и собрать общий отчёт."""
    cases = load_test_cases()
    if args.id:
        cases = [c for c in cases if c.id == args.id]
    cases = [with_universal_checks(c) for c in cases]
    if not cases:
        print("нет тест-кейсов для запуска", file=sys.stderr)
        return 1

    if args.model:  # None — дефолт AgentSession
        print(f"[run] model={args.model}")

    makedirs(runs_root, WORK_DIRS_DIR)
    semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)

    results: List[RunResult] = []
    success = 0
    total_work = len(cases) * max(1, args.runs)
    with tqdm(total=total_work, unit="кейс", desc="прогон кейсов") as pbar:
        for i in range(1, args.runs + 1):
            for case in cases:
                pbar.set_description(f"run {i}/{args.runs}: {case.id}")
                try:
                    rr = await run_case(case, runs_root, semaphore, model=args.model, run_num=i)
                    rr.run = i
                    results.append(rr)
                    success += 1
                except Exception as e:  # не прерываем остальные кейсы
                    print(f"[run:{case.id}] FAILED: {e}", file=sys.stderr)
                pbar.update(1)

    # Общий xlsx по всем кейсам.
    if results:
        result_xlsx = write_results_xlsx(results, runs_root / "results.xlsx")
        for rr in results:
            rr.judge_xlsx = result_xlsx
        print(f"[run] result_xlsx={result_xlsx}")

    # Сводка по метрикам на каждый кейс.
    print("метрики по кейсам:")
    for rr in results:
        print(f"  [{rr.test_case.id}] (run {rr.run})")
        print_metrics_summary(rr)

    print(f"done: {success}/{len(cases)}")
    return 0 if success == len(cases) else 1


async def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv()

    # Куки протухают за ~13 ч — обновляем на каждый прогон.
    refresh_cookies()

    args = _parse_args(argv)

    if args.list_models:
        print("Модели (provider/model):")
        for m in list_models():
            print(f"  {m}")
        return 0

    print(f"Количество прогонов: {args.runs}")

    # Каждый запуск пишет в runs/yyyy-mm-dd_hh-mm/.
    runs_root = RUNS_DIR / datetime.now().strftime("%Y-%m-%d_%H-%M")

    # Проверка готового файла без агента.
    if args.validate:
        return await _validate_mode(args, runs_root)
    return await _full_run_mode(args, runs_root)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))