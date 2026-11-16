"""Взвешенная итоговая оценка всех сценариев и one_number_result.txt.

S агрегирует достоверные PASS/FAIL по метрикам с нормализацией весов. Routing,
время и контекст выводятся отдельно и не участвуют в S; ошибки judge не
считаются отрицательными вердиктами.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from pathlib import Path
from typing import List

from .models import RunResult


def calculate_one_number(results: List[RunResult]) -> dict:
    metrics = OrderedDict()
    for result in results:
        for check_result in result.check_results:
            if check_result.error:
                continue
            name = check_result.check.metric.strip()
            if not name:
                continue
            weight = 1.0 if check_result.check.weight is None else float(check_result.check.weight)
            metric = metrics.setdefault(name, {"weight": weight, "pass": 0, "all": 0})
            if not math.isclose(metric["weight"], weight):
                raise ValueError(f"Конфликтующие веса у метрики {name!r}: {metric['weight']} и {weight}")
            metric["all"] += 1
            metric["pass"] += int(check_result.success)

    available = {name: value for name, value in metrics.items() if value["all"] > 0}
    weight_sum = sum(value["weight"] for value in available.values())
    score = None
    if available and weight_sum > 0:
        score = 0.0
        for value in available.values():
            value["normalized_weight"] = value["weight"] / weight_sum
            value["p"] = value["pass"] / value["all"]
            score += value["normalized_weight"] * value["p"]
    else:
        for value in available.values():
            value["normalized_weight"] = 0.0
            value["p"] = 0.0

    routed = [result for result in results if result.routing_applicable]
    return {
        "score": score,
        "metrics": available,
        "routing_pass": sum(result.routing_success for result in routed),
        "routing_all": len(routed),
        "average_time": (
            sum(result.agent_time_sec for result in routed) / len(routed)
            if routed else None
        ),
        "max_context_tokens": max((result.context_tokens for result in results), default=0),
        "max_context_limit": max((result.context_limit for result in results), default=0),
        "max_context_percent": max((result.context_percent for result in results), default=0.0),
    }


def render_one_number(results: List[RunResult]) -> str:
    summary = calculate_one_number(results)
    lines = [
        r"\[",
        r"\boxed{",
        r"S = \sum_{i=1}^{n} w_i \cdot p_i,",
        r"\quad p_i = \frac{\text{pass}_i}{\text{all}_i},",
        r"\quad \sum_{i=1}^{n} w_i = 1",
        r"}",
        r"\]",
        "",
        f"S = {summary['score']:.6f}" if summary["score"] is not None else "S = NA",
        "",
    ]
    for name, metric in summary["metrics"].items():
        lines.extend([
            f"[{name}]",
            f"weight = {metric['normalized_weight']:.6f}",
            f"pass = {metric['pass']}",
            f"all = {metric['all']}",
            f"p = {metric['p']:.6f}",
            "",
        ])
    lines.append(
        f"routing = {summary['routing_pass']}/{summary['routing_all']} "
        "(в S не участвует)"
    )
    lines.append(
        f"average_agent_time_sec = {summary['average_time']:.3f}"
        if summary["average_time"] is not None else "average_agent_time_sec = NA"
    )
    lines.extend([
        f"max_context_tokens = {summary['max_context_tokens']}",
        f"max_context_limit = {summary['max_context_limit']}",
        f"max_context_percent = {summary['max_context_percent']:.1f}",
        "",
    ])
    return "\n".join(lines)


def write_one_number_result(results: List[RunResult], out_path: Path) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_one_number(results), encoding="utf-8")
    return str(out_path)
