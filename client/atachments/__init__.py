"""Вложения пользователя в промпте: файл задаётся путём, клиент делает остальное.

Снаружи это одна функция: путь к файлу -> части сообщения, готовые к отправке
в `/session/:id/prompt_async`, ровно такие, какие отправил бы desktop UI
(см. `bridge.py` и модули desktop-ai-app, которые он переиспользует).
"""
from __future__ import annotations

from .bridge import (
    CSV_INLINE_CHAR_LIMIT,
    CSV_MIME,
    DOCX_MIME,
    MAX_ATTACHMENT_BYTES,
    MAX_IMAGE_ATTACHMENT_BYTES,
    PPTX_MIME,
    PreparedAttachment,
    attachment_mime,
    build_message_parts,
    file_url,
    prepare_attachment,
    prepare_attachments,
    resolve_attachments,
    resolve_parts_via_server,
)

__all__ = [
    "CSV_INLINE_CHAR_LIMIT",
    "CSV_MIME",
    "DOCX_MIME",
    "MAX_ATTACHMENT_BYTES",
    "MAX_IMAGE_ATTACHMENT_BYTES",
    "PPTX_MIME",
    "PreparedAttachment",
    "attachment_mime",
    "build_message_parts",
    "file_url",
    "prepare_attachment",
    "prepare_attachments",
    "resolve_attachments",
    "resolve_parts_via_server",
]