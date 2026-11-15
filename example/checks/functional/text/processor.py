from __future__ import annotations

from pathlib import Path


class Processor:
    extensions = {".md", ".txt", ".json", ".csv", ".html", ".svg", ".log"}

    def extract_text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def render(self, path: str, output_dir: Path) -> list[str]:
        return []
