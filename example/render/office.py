"""Рендер Office-артефакта в PNG: LibreOffice -> PDF -> pdftoppm и пост-контроль.

Исходник рендерится как есть (вмешиваемся только в шрифты, см. fonts.py):
авто-коррекция вёрстки маскировала бы дефекты агента, а наложения должны
оставаться видимыми судье. Пост-контроль: число страниц и пустые слайды.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Union

from PIL import Image, ImageStat

from . import LIBREOFFICE, SUPPORTED_EXTENSIONS
from .fonts import (
    collect_pptx_fonts,
    font_substitution_warning,
    force_substituted,
    is_fontconfig_isolated,
    isolate_fontconfig,
    missing_fonts,
    seed_lo_profile_fonts,
)

# Пороги детекта пустого слайда: почти нулевая дисперсия яркости и почти
# нет уникальных оттенков.
_BLANK_STD_THRESHOLD = 4.0
_BLANK_MIN_COLORS = 12


def _is_blank_png(path: Path) -> bool:
    """Пустой/битый слайд: почти однотонная картинка."""
    try:
        with Image.open(path) as im:
            gray = im.convert("L")
            gray.thumbnail((360, 360))  # ускорение, статистика устойчива
            std = ImageStat.Stat(gray).stddev[0]
            colors = sum(1 for v in gray.histogram() if v > 0)
        return std < _BLANK_STD_THRESHOLD and colors < _BLANK_MIN_COLORS
    except Exception:
        return True


def _count_pptx_slides(file_path: Path) -> int:
    """Число слайдов в .pptx по zip-структуре (без python-pptx)."""
    try:
        with zipfile.ZipFile(file_path) as zf:
            return sum(
                1 for n in zf.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)
            )
    except Exception:
        return 0


def _clear_render_pngs(output_dir: Path) -> None:
    """Удалить прежние PNG рендера (page-*)."""
    for old_file in output_dir.glob("page-*.png"):
        old_file.unlink()


def _natural_page_sort_key(filename: str):
    stem = Path(filename).stem
    number = stem.split("-")[-1]
    try:
        return int(number)
    except ValueError:
        return 0


def _convert_with_libreoffice(src: Path, temp_dir: Path,
                              missing_fonts_list: Optional[List[str]] = None,
                              timeout: int = 300,
                              lo_profile: Optional[Path] = None
                              ) -> Optional[Path]:
    """PPTX/DOCX/XLSX -> PDF через LibreOffice в чистом профиле.

    Отсутствующие шрифты заменяются копиями в user/fonts профиля; готовый
    профиль можно передать в lo_profile.
    """
    if lo_profile is None:
        lo_profile = temp_dir / f"lo_profile_{uuid.uuid4().hex[:8]}"
        lo_profile.mkdir(parents=True, exist_ok=True)
        if missing_fonts_list:
            seed_lo_profile_fonts(lo_profile, missing_fonts_list)
    command = [
        LIBREOFFICE,
        f"-env:UserInstallation=file://{lo_profile}",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(temp_dir),
        str(src),
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    pdf_path = temp_dir / f"{src.stem}.pdf"
    if result.returncode != 0 or not pdf_path.exists():
        return None
    return pdf_path


def _pdf_to_pngs(pdf_path: Path, output_dir: Path, dpi: int) -> List[Path]:
    """PDF -> PNG-страницы через pdftoppm, в натуральном порядке."""
    if shutil.which("pdftoppm") is None:
        raise RuntimeError("pdftoppm not found. Install poppler-utils.")
    output_prefix = output_dir / "page"
    result = subprocess.run(
        [
            "pdftoppm",
            "-png",
            "-r",
            str(dpi),
            str(pdf_path),
            str(output_prefix),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        return []
    return sorted(
        output_dir.glob("page-*.png"),
        key=lambda p: _natural_page_sort_key(p.name),
    )


def render_office_file(
    file_path: Union[str, Path],
    output_dir: Union[str, Path] | None = None,
    dpi: int = 150,
) -> Dict[str, Union[bool, List[str]]]:
    """Открыть Office-файл и сохранить страницы в PNG.

    output_dir=None — рядом с исходником (<filename>_rendered/). dpi по
    умолчанию 150: крупные PNG даунскейлятся vLLM, и мелкий текст слайда
    становится нечитаем для судьи. Возвращает {"correct", "photos_path",
    "renderer", "warnings"}.
    """
    file_path = Path(file_path).resolve()

    if not file_path.exists():
        return {"correct": False, "photos_path": []}

    if not file_path.is_file():
        return {"correct": False, "photos_path": []}

    suffix = file_path.suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {suffix}. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    if output_dir is None:
        output_dir = file_path.parent / f"{file_path.stem}_rendered"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_render_pngs(output_dir)

    warnings: List[str] = []

    # Подмена шрифта меняет метрики строк — отсюда «лишние отступы».
    missing_font_list: List[str] = []
    deck_fonts: set = set()
    forced_fonts: List[str] = []
    if suffix == ".pptx":
        deck_fonts = collect_pptx_fonts(file_path)
        forced_fonts = force_substituted(deck_fonts)

    with tempfile.TemporaryDirectory(prefix="office_render_") as temp_dir:
        temp_dir = Path(temp_dir)
        # Детект отсутствующих шрифтов — после изоляции fontconfig,
        # чтобы force-семейства были уже скрыты и попали в missing.
        if suffix == ".pptx":
            if forced_fonts:
                isolate_fontconfig(forced_fonts, temp_dir)
            missing_font_list = missing_fonts(deck_fonts)
            if missing_font_list and not is_fontconfig_isolated():
                # Даём копиям-заменам fontconfig-видимый каталог.
                isolate_fontconfig([], temp_dir)
            if missing_font_list:
                warnings.append(
                    font_substitution_warning(missing_font_list, forced_fonts))
        try:
            if suffix == ".pdf":
                pdf_path = file_path
                renderer = "pdf"
            else:
                # Рендерим исходник как есть; подстановка шрифтов — внутри
                # _convert_with_libreoffice.
                if not Path(LIBREOFFICE).exists():
                    raise RuntimeError(f"LibreOffice not found at {LIBREOFFICE}")
                pdf_path = _convert_with_libreoffice(
                    file_path, temp_dir, missing_fonts_list=missing_font_list)
                renderer = "libreoffice"

                if pdf_path is None:
                    return {"correct": False, "photos_path": [], "warnings": warnings}

            photos = _pdf_to_pngs(pdf_path, output_dir, dpi)
            if not photos:
                return {"correct": False, "photos_path": [], "warnings": warnings}

            # Пост-контроль: число страниц и пустота.
            if suffix == ".pptx":
                expected_slides = _count_pptx_slides(file_path)
                if expected_slides and len(photos) != expected_slides:
                    warnings.append(
                        f"{renderer}: отрендерено {len(photos)} страниц, "
                        f"в исходнике {expected_slides} слайдов"
                    )
            blank_pages = [
                i for i, p in enumerate(photos, start=1) if _is_blank_png(p)
            ]
            if blank_pages:
                warnings.append(
                    f"{renderer}: пустые страницы {blank_pages} "
                    f"(если в исходнике там контент — рендер неполный)"
                )

            return {
                "correct": True,
                "photos_path": [str(p.resolve()) for p in photos],
                "renderer": renderer,
                "warnings": warnings,
            }

        except subprocess.TimeoutExpired:
            return {"correct": False, "photos_path": [], "warnings": warnings}
        except Exception:
            return {"correct": False, "photos_path": [], "warnings": warnings}