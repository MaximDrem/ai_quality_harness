"""Пути, константы пайплайна и загрузка `.env`.

Кейсы лежат в `example/data`, метрики — в `example/metrics`, код проверок и
процессоры — в `example/checks`. Результаты прогонов и рабочие каталоги
агента вынесены за пределы репозитория (`TEST_AGENT_ROOT.parent`).
`load_dotenv()` вызывается первым делом в CLI: конфиг судьи
(`judge/client.py`) читает переменные окружения в момент импорта, поэтому
`.env` должен быть в `os.environ` до него.
"""
from __future__ import annotations

import os
from pathlib import Path

# Каталог пакета (example/) и корень репозитория.
EXAMPLE_DIR = Path(__file__).resolve().parent
TEST_AGENT_ROOT = EXAMPLE_DIR.parent

# Результаты прогонов: <parent>/lib-ai-quality-harness-runs/<yyyy-mm-dd_hh-mm>_<model>/run_<n>/<case_id>/.
# Каталог лежит рядом с репозиторием, а не внутри него: `work_dir` кейса —
# это cwd sidecar'а, и `.git` в родителях переключает его в git-режим
# (`Is directory a git repo: yes` и блок git-правил в system prompt). В
# desktop чаты живут вне репозитория, поэтому прогоны должны быть такими же.
RUNS_DIR = TEST_AGENT_ROOT.parent / "lib-ai-quality-harness-runs"

# Служебные рабочие каталоги вне дерева конкретного прогона (например,
# пробник каталога моделей для `--list-models`). Лежат в корне прогонов,
# чтобы не плодить папки ни в репозитории, ни рядом с ним.
WORK_DIRS_DIR = RUNS_DIR / "work_dirs"

# Входные данные: тест-кейсы и отдельно описанные группы метрик.
CASES_DIR = EXAMPLE_DIR / "data"
METRICS_DIR = EXAMPLE_DIR / "metrics"

# Общий конфиг wsc, с которым работает desktop (~/.wsc). Headless
# поднимается так же, как `bun run dev:desktop`; справочный путь.
CONFIG_BASE_DIR = Path.home() / ".wsc"

# Ограничение числа одновременно работающих инстансов агента.
SEMAPHORE_LIMIT = 1

# Расширения артефактов, которые умеет рендерить render/.
RENDER_SUPPORTED = {".pptx", ".docx", ".xlsx", ".pdf"}

# Ограничение длины текста артефакта, отдаваемого текстовому судью (символы).
JUDGE_TEXT_MAX_CHARS = 20000


def case_runs_dir(runs_root: Path, run_num: int, case_id: str) -> Path:
    """Каталог прогона кейса: `<runs_root>/run_<n>/<case_id>/`."""
    return runs_root / f"run_{run_num}" / case_id


def load_dotenv() -> None:
    """Загрузить переменные окружения из `.env` в корне репозитория.

    Уже установленные переменные не перезаписываются.
    """
    dotenv = TEST_AGENT_ROOT / ".env"
    if not dotenv.exists():
        return
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value