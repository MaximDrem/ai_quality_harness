from __future__ import annotations

from pathlib import Path

from example.render.office import render_office_file


class Processor:
    extensions = {".docx", ".xlsx", ".pdf"}

    def extract_text(self, path: str) -> str:
        from example.judge.extract import _parse_office_artifact_text

        return _parse_office_artifact_text(path)

    def render(self, path: str, output_dir: Path) -> list[str]:
        result = render_office_file(path, output_dir)
        return list(result.get("photos_path", [])) if result.get("correct") else []
