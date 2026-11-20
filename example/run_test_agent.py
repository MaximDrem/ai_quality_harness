"""CLI прогона YAML-кейсов и валидации готовых артефактов.

Поддерживает выбор сценария, модели OpenCode, auth_mode и skills_dir. Полный
прогон создаёт XLSX и one_number_result.txt; справка и валидация не обновляют
cookies и не запускают sidecar.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from tqdm import tqdm

from .agent.cookies import refresh_cookies
from .agent.session import run_case
from .cases import DEFAULT_SCENARIO, available_scenarios, load_test_cases, makedirs
from .config import RUNS_DIR, SEMAPHORE_LIMIT, load_dotenv
from .evaluate import case_for_validate, list_models, validate_artifact, with_universal_checks
from .models import RunResult
from .report import write_results_xlsx
from .scoring import write_one_number_result


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", default=None, help="фильтр по id кейса")
    parser.add_argument("--scenario", default=None, help=f"сценарий из example/data (по умолчанию {DEFAULT_SCENARIO})")
    parser.add_argument("--list-scenarios", action="store_true", help="показать автоматически найденные сценарии")
    parser.add_argument("--model", default=None,
                        help="модель OpenCode: provider/model или имя модели (провайдер подставится сам)")
    parser.add_argument("--auth-mode", choices=("ift", "prod", "cert"), default=None)
    parser.add_argument("--skills-dir", default=None, help="OPENCODE_CONFIG_DIR с skills/, agent/ и настройками")
    parser.add_argument("--list-models", action="store_true", help="показать доступные модели и выйти")
    parser.add_argument("--validate", default=None, help="проверить готовый артефакт без запуска агента")
    parser.add_argument("--case", default=None, help="id кейса, чей чек-лист использовать для --validate")
    parser.add_argument("--checks", default=None, help="text-проверки для --validate, по одной на строку")
    parser.add_argument("--runs", type=int, default=1, help="количество прогонов набора")
    return parser.parse_args(argv)


def model_slug(model: str) -> str:
    name = str(model or "default").strip().rsplit("/", 1)[-1]
    slug = re.sub(r"[^A-Za-z0-9._]+", "_", name).strip("._")
    return slug or "default"


def print_metrics_summary(result: RunResult) -> None:
    aggregated: Dict[str, List[int]] = collections.OrderedDict()
    errors = 0
    for check_result in result.check_results:
        if check_result.error:
            errors += 1
            continue
        key = check_result.check.metric or "—"
        aggregated.setdefault(key, [0, 0])
        aggregated[key][1] += 1
        if check_result.success:
            aggregated[key][0] += 1
    for metric, (passed, total) in aggregated.items():
        flag = "OK " if passed == total and total else "!! "
        print(f"    {flag}[{metric}] {passed}/{total}")
    if errors:
        print(f"    ?? [ОШИБКИ СУДЬИ] {errors} — в метрики не входят")


async def _validate_mode(args: argparse.Namespace, runs_root: Path) -> int:
    case = case_for_validate(args.validate, args.case, args.checks, scenario=args.scenario)
    print(f"[validate:{case.id}] файл={args.validate}")
    result = await validate_artifact(case, args.validate, runs_root)
    result_xlsx = write_results_xlsx([result], runs_root / "run_1" / case.id / "result.xlsx")
    result.judge_xlsx = result_xlsx
    passed = [item for item in result.check_results if item.success]
    errored = [item for item in result.check_results if not item.success and item.error]
    failed = [item for item in result.check_results if not item.success and not item.error]
    print(f"[validate:{case.id}] прошло {len(passed)}/{len(result.check_results) - len(errored)}"
          f" (ошибок судьи: {len(errored)}); результат: {result_xlsx}")
    for item in failed:
        print(f"  [FAIL] {item.check.text}\n         {item.reason}")
    for item in errored:
        print(f"  [ERROR] {item.check.text}\n         {item.reason}")
    for item in passed:
        print(f"  [PASS] {item.check.text}")
    print(f"[validate:{case.id}] метрики:")
    print_metrics_summary(result)
    return 0 if not failed else 1


def _case_scenarios(case_id: str, skip: Optional[str] = None) -> List[str]:
    """Сценарии, в которых есть кейс с таким id (для подсказки при пустом фильтре)."""
    hits: List[str] = []
    for name in available_scenarios():
        if name == skip:
            continue
        try:
            ids = {case.id for case in load_test_cases(scenario=name)}
        except Exception:
            continue
        if case_id in ids:
            hits.append(name)
    return hits


async def _full_run_mode(args: argparse.Namespace, runs_root: Path) -> int:
    cases = load_test_cases(scenario=args.scenario)
    if args.id:
        cases = [case for case in cases if case.id == args.id]
    cases = [with_universal_checks(case) for case in cases]
    if not cases:
        print("нет тест-кейсов для запуска", file=sys.stderr)
        if args.id:
            hits = _case_scenarios(args.id, skip=args.scenario or DEFAULT_SCENARIO)
            if hits:
                print(
                    f"  кейс {args.id} есть в сценарии {', '.join(hits)}: "
                    f"добавьте --scenario {hits[0]}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  кейс {args.id} не найден; сценарии: {', '.join(available_scenarios())}",
                    file=sys.stderr,
                )
        else:
            print(
                f"  сценарий {args.scenario or DEFAULT_SCENARIO} пуст; "
                f"сценарии: {', '.join(available_scenarios())}",
                file=sys.stderr,
            )
        return 1
    if args.model:
        print(f"[run] model={args.model}")

    makedirs(runs_root)
    semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)
    results: List[RunResult] = []
    total_work = len(cases) * max(1, args.runs)
    success = 0
    with tqdm(total=total_work, unit="кейс", desc="прогон кейсов") as progress:
        for run_num in range(1, args.runs + 1):
            for case in cases:
                progress.set_description(f"run {run_num}/{args.runs}: {case.id}")
                try:
                    result = await run_case(
                        case,
                        runs_root,
                        semaphore,
                        model=args.model,
                        run_num=run_num,
                        auth_mode=args.auth_mode,
                        skills_dir=args.skills_dir,
                    )
                    result.run = run_num
                    results.append(result)
                    success += 1
                except Exception as error:
                    print(f"[run:{case.id}] FAILED: {error}", file=sys.stderr)
                progress.update(1)

    if results:
        result_xlsx = write_results_xlsx(results, runs_root / "results.xlsx")
        one_number = write_one_number_result(results, runs_root / "one_number_result.txt")
        for result in results:
            result.judge_xlsx = result_xlsx
        print(f"[run] result_xlsx={result_xlsx}")
        print(f"[run] one_number_result={one_number}")
    print("метрики по кейсам:")
    for result in results:
        print(f"  [{result.test_case.id}] (run {result.run})")
        print_metrics_summary(result)
    print(f"done: {success}/{total_work}")
    return 0 if success == total_work else 1


def _effective_model(args: argparse.Namespace) -> str:
    if args.model:
        return args.model
    from client import ClientConfig

    kwargs = {"auth_mode": args.auth_mode} if args.auth_mode else {}
    return ClientConfig(**kwargs).resolve().model


async def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    if args.list_scenarios:
        for scenario in available_scenarios():
            print(scenario)
        return 0

    load_dotenv()
    if args.list_models:
        refresh_cookies(args.auth_mode)
        print("Модели (provider/model):")
        for model in list_models(auth_mode=args.auth_mode, skills_dir=args.skills_dir):
            print(f"  {model}")
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    if args.validate:
        return await _validate_mode(args, RUNS_DIR / stamp)

    model = _effective_model(args)
    refresh_cookies(args.auth_mode)
    runs_root = RUNS_DIR / f"{stamp}_{model_slug(model)}"
    return await _full_run_mode(args, runs_root)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
