"""Рендер Office-артефактов в PNG: LibreOffice -> PDF -> pdftoppm."""
from __future__ import annotations

# LibreOffice вызывается по абсолютному пути (headless-конвертация).
LIBREOFFICE = "/usr/bin/libreoffice"

# Расширения, которые умеет рендерить этот пакет.
SUPPORTED_EXTENSIONS = {".pptx", ".pdf", ".xlsx", ".docx"}