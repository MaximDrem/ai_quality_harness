"""Пути, константы пайплайна и загрузка `.env`.

Все выходные каталоги привязаны к корню проекта (`TEST_AGENT_ROOT`).
Кейсы лежат в `example/data`, метрики — в `example/metrics`, код проверок и
процессоры — в `example/checks`.
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

# Результаты прогонов: runs/<yyyy-mm-dd_hh-mm>_<model>/run_<n>/<case_id>/.
RUNS_DIR = TEST_AGENT_ROOT / "runs"

# Рабочие каталоги агентов вне дерева результатов.
WORK_DIRS_DIR = TEST_AGENT_ROOT / "work_dirs"

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