"""Общая обработка Office-текста и рендера в rendered_photos."""

from __future__ import annotations

from pathlib import Path

from example.render.office import render_office_file

from .base import BaseProcessor


class OfficeProcessor(BaseProcessor):
    extensions = set()

    def build_text(self, artifact) -> str:
        from example.judge.extract import _parse_office_artifact_text

        return _parse_office_artifact_text(artifact.path)

    def build_raw_structure(self, artifact):
        return None

    def build_rendered_photos(self, artifact) -> list[str]:
        output_dir = Path(artifact.render_dir) if artifact.render_dir else Path(artifact.path).parent / "render_artifact"
        result = render_office_file(artifact.path, output_dir)
        return list(result.get("photos_path", [])) if result.get("correct") else []
