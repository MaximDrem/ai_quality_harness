from __future__ import annotations

from pathlib import Path
from typing import List

from .checks.functional._helpers import build_results_workbook
from .models import RunResult


def write_results_xlsx(results: List[RunResult], out_path: Path) -> str:
    workbook = build_results_workbook(results)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(out_path)
    return str(out_path)
