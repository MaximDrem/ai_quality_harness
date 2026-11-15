"""Мост к модулям desktop-ai-app, которые готовят вложения пользователя.

UI desktop (packages/app) при отправке сообщения не пересылает файл «как
есть»: он готовит части (`buildRequestParts` / `buildUploadedAttachmentRequestParts`)
и отправляет их в `/session/:id/prompt_async`, а сервер (`SessionPrompt.resolvePart`)
материализует вложения — копирует файл в `uploads/<session>/<message>/`,
извлекает текст Office и раскладывает части в том порядке, который реально
уходит в модель.

Чтобы клиент вёл себя 1:1 с UI, здесь две половины:

  * `prepare` — повторяет клиентскую подготовку UI для файла, заданного путём
    (pptx → ссылка на файл, xlsx/xls → CSV-«пилюля», pdf/изображение/прочее →
    data-URL + извлечённый текст);
  * `resolve` — отдаёт части в настоящий `SessionPrompt` бэкенда через
    bun-мост `bridge.ts` (сервер сам материализует файлы и извлекает текст).

bun-мост запускается из `packages/opencode` (там живут модули и их алиасы
`@/...`); desktop-ai-app при этом не меняется — модули только импортируются.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.errors import AgentError
from ..core.ids import ascending

# MIME, с которыми UI работает особым образом (см. packages/app/src/constants/file-picker.ts).
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
CSV_MIME = "text/csv"

# UI отдаёт презентации ссылкой на файл, а не base64: крупные колоды с медиа
# раздувают renderer. Ниже этого размера поведение то же (ссылка на файл), но
# клиент проверяет размер как UI: 25 MB для обычных файлов, 15 MB для картинок.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_IMAGE_ATTACHMENT_BYTES = 15 * 1024 * 1024

_SPREADSHEET_EXTS = {"xlsx", "xls"}
_IMAGE_EXTS = {
    "gif": "image/gif",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}
_IMAGE_MIMES = set(_IMAGE_EXTS.values())
_OFFICE_EXTS = {"docx": DOCX_MIME, "pptx": PPTX_MIME}
_OFFICE_MIMES = {DOCX_MIME, PPTX_MIME}
_TEXT_MIMES = {
    "application/json",
    "application/ld+json",
    "application/toml",
    "application/x-toml",
    "application/x-yaml",
    "application/xml",
    "application/yaml",
}
_SNIFF_BYTES = 4096

# UI вставляет содержимое таблицы в промпт только до этого размера; длиннее —
# пилюля уходит с пометкой «оставлен файлом» (`CSV_INLINE_CHAR_LIMIT`).
CSV_INLINE_CHAR_LIMIT = 120_000

_BRIDGE_TIMEOUT = 120.0


def _ext(name: str) -> str:
    idx = name.rfind(".")
    return "" if idx == -1 else name[idx + 1 :].lower()


def _text_mime(mime: str) -> bool:
    if not mime:
        return False
    if mime.startswith("text/"):
        return True
    if mime in _TEXT_MIMES:
        return True
    return mime.endswith("+json") or mime.endswith("+xml")


def _text_bytes(sample: bytes) -> bool:
    if not sample:
        return True
    weird = 0
    for byte in sample:
        if byte == 0:
            return False
        if byte < 9 or (13 < byte < 32):
            weird += 1
    return weird / len(sample) <= 0.3


def attachment_mime(path: Path) -> Optional[str]:
    """MIME вложения по пути — те же правила, что `attachmentMime` в UI.

    UI получает от браузера `File.type`, но для файла с диска этот тип пуст,
    поэтому решение принимается по расширению и «сниффингу» первых 4 КБ:
    текст — `text/plain`, бинарь без известного расширения — не поддерживается.
    """
    suffix = _ext(path.name)
    office = _OFFICE_EXTS.get(suffix)
    if office:
        return office
    if suffix in _SPREADSHEET_EXTS:
        return CSV_MIME
    fallback = _IMAGE_EXTS.get(suffix) or ("application/pdf" if suffix == "pdf" else None)
    if fallback:
        return fallback
    with path.open("rb") as fh:
        sample = fh.read(_SNIFF_BYTES)
    if not _text_bytes(sample):
        return None
    return "text/plain"


def file_url(path: Path) -> str:
    """`file://`-ссылка на абсолютный путь (как `fileUrlFromPath` в UI).

    `Path.as_uri()` кодирует `@` как `%40`, а desktop — нет: он берёт
    `encodeURI` и дополнительно экранирует только `#` и `?` (домашний каталог
    с `@` в имени в URL остаётся как есть). Ссылка должна совпадать байт в байт,
    иначе поведение `file:`-ветки сервера расходится с desktop.
    """
    resolved = str(path.resolve())
    if re.match(r"^[A-Za-z]:[\\/]", resolved):
        return "file:///" + _encode_file_url_path(resolved.replace("\\", "/"))
    if resolved.startswith("\\\\"):
        return "file://" + _encode_file_url_path(resolved[2:].replace("\\", "/"))
    return "file://" + _encode_file_url_path(resolved)


def _encode_file_url_path(value: str) -> str:
    return urllib.parse.quote(value, safe="/@:,+=&~()!'$*[];").replace("#", "%23").replace("?", "%3F")


def get_filename(path: str) -> str:
    """Имя файла из пути (как `getFilename` в @opencode-ai/core/util/path)."""
    trimmed = re.sub(r"[/\\]+$", "", path)
    return re.split(r"[/\\]", trimmed)[-1]


@dataclass
class PreparedAttachment:
    """Вложение, подготовленное ровно так, как это делает UI перед отправкой."""

    path: Path
    mime: str
    filename: str
    # Части, которые UI поставит в промпт (text/file), ещё до серверной
    # материализации.
    parts: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "mime": self.mime,
            "filename": self.filename,
            "parts": [dict(p) for p in self.parts],
        }


def _data_url(path: Path, mime: str) -> str:
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _spreadsheet_as_csv(path: Path) -> Optional[str]:
    """xlsx/xls -> CSV по всем листам (`# Sheet: <name>`), как в UI.

    UI делает это в renderer через `@datalens-tech/xlsx`; чтобы не тащить
    node-зависимость в клиент, разбор идёт тем же пакетом через bun — он уже
    есть в node_modules desktop-ai-app.
    """
    from ..core.config import ClientConfig

    repo = _root()
    helper = Path(__file__).with_name("spreadsheet.ts")
    result = subprocess.run(
        [
            os.path.expandvars(ClientConfig().bun),
            "run",
            str(helper),
            str(path),
            str(repo / "packages" / "app"),
            "@datalens-tech/xlsx",
        ],
        capture_output=True,
        text=True,
        timeout=_BRIDGE_TIMEOUT,
        cwd=str(repo),
    )
    if result.returncode != 0:
        raise AgentError(f"failed to read spreadsheet {path.name}: {result.stderr.strip()[:500]}")
    payload = json.loads(result.stdout or "{}")
    return payload.get("csv")


def _root() -> Path:
    """Корень desktop-ai-app (там лежат node_modules с `@datalens-tech/xlsx`)."""
    from ..core.config import _root as repo_root

    return Path(repo_root()).resolve()


def prepare_attachment(raw_path: str, base_dir: Optional[Path] = None) -> PreparedAttachment:
    """Подготовить файл к отправке так, как это делает UI.

    `base_dir` — каталог, относительно которого понимается путь (обычно
    work_dir сессии), как относительные пути в промпте desktop.
    """
    path = Path(raw_path).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    path = path.resolve()
    if not path.exists():
        raise AgentError(f"attachment not found: {raw_path}")
    if path.is_dir():
        raise AgentError(f"attachment is a directory: {path}")

    filename = path.name
    size = path.stat().st_size
    mime = attachment_mime(path)
    if mime is None:
        raise AgentError(f"unsupported attachment type: {filename}")

    is_image = mime.startswith("image/")
    limit = MAX_IMAGE_ATTACHMENT_BYTES if is_image else MAX_ATTACHMENT_BYTES

    # Колода отправляется data-URL'ом с `ignored: true` (`officeTextMode` в UI):
    # сервер сохраняет её в uploads и рядом с путём кладёт извлечённый текст
    # слайдов, по которому модель и отвечает. Ссылка `file://` тоже сохраняет
    # файл, но без текста; колода, которую нельзя прочитать целиком (386, 52 МБ),
    # уходит именно ссылкой — так её и отправлял UI.
    if mime in (PPTX_MIME, DOCX_MIME) and size <= MAX_ATTACHMENT_BYTES:
        parts = [
            {
                "type": "file",
                "mime": mime,
                "filename": filename,
                "url": _data_url(path, mime),
                "ignored": True,
                "localPath": str(path),
            }
        ]
        return PreparedAttachment(path=path, mime=mime, filename=filename, parts=parts)

    if mime == PPTX_MIME:
        parts = [
            {
                "type": "file",
                "mime": PPTX_MIME,
                "filename": filename,
                "url": file_url(path),
                "localPath": str(path),
            }
        ]
        return PreparedAttachment(path=path, mime=PPTX_MIME, filename=filename, parts=parts)

    if size > limit:
        raise AgentError(
            f"attachment too large: {filename} ({size} bytes, limit {limit})"
        )

    # Таблицы UI превращает в CSV-«пилюлю»: файл-часть с mime text/csv и
    # синтетический текстовый блок с содержимым (text-only модели иначе
    # таблицу не увидят). Содержимое длиннее `CSV_INLINE_CHAR_LIMIT` в контекст
    # не вставляется — та же пилюля уходит вместе с пояснительной пометкой.
    if mime == CSV_MIME:
        csv = _spreadsheet_as_csv(path)
        if not csv:
            raise AgentError(f"spreadsheet has no readable sheets: {filename}")
        if len(csv) <= CSV_INLINE_CHAR_LIMIT:
            inline = f"\n\n[{filename}]\n```csv\n{csv}\n```\n"
        else:
            inline = (
                f"\n\n[{filename}]\nCSV content is {len(csv):,} characters, so it was kept as an "
                "attached file instead of being pasted into the model context. Use the saved "
                "attachment file from the prompt context to inspect or process the full data.\n"
            )
        parts = [
            {
                "type": "file",
                "mime": CSV_MIME,
                "filename": filename,
                "url": "data:text/csv;base64," + base64.b64encode(csv.encode("utf-8")).decode("ascii"),
            },
            {"type": "text", "synthetic": True, "text": inline},
        ]
        return PreparedAttachment(path=path, mime=CSV_MIME, filename=filename, parts=parts)

    # Остальное (картинки, PDF, Office, текст) UI отправляет data-URL'ом; сервер
    # сам решает, что из этого материализовать в uploads, что извлечь текстом,
    # а что отдать провайдеру как файл.
    parts = [
        {
            "type": "file",
            "mime": mime,
            "filename": filename,
            "url": _data_url(path, mime),
            "localPath": str(path),
        }
    ]
    return PreparedAttachment(path=path, mime=mime, filename=filename, parts=parts)


def prepare_attachments(paths: List[str], base_dir: Optional[Path] = None) -> List[PreparedAttachment]:
    return [prepare_attachment(p, base_dir=base_dir) for p in paths]


def build_message_parts(message: str, attachments: List[PreparedAttachment]) -> List[Dict[str, Any]]:
    """Части сообщения в том порядке, в котором их шлёт UI: текст, вложения."""
    from ..core import ids as _ids

    def with_id(part: Dict[str, Any], key: str) -> Dict[str, Any]:
        return {"id": _ids.ascending(key), **part}

    parts: List[Dict[str, Any]] = [with_id({"type": "text", "text": message}, "part")]
    for attachment in attachments:
        for part in attachment.parts:
            parts.append(with_id(part, "part"))
    return parts


# --------------------------------------------------------------- bun-мост
def bridge_dir() -> Path:
    return Path(__file__).resolve().parent


def _bun_binary() -> str:
    from ..core.config import ClientConfig

    return os.path.expandvars(ClientConfig().bun)


def resolve_parts_via_server(
    *,
    session_id: str,
    message_id: str,
    work_dir: str,
    parts: List[Dict[str, Any]],
    agent: str = "build",
    model: Optional[Dict[str, str]] = None,
    base_dir: Optional[Path] = None,
    timeout: float = _BRIDGE_TIMEOUT,
) -> List[Dict[str, Any]]:
    """Прогнать части через настоящий `SessionPrompt` бэкенда и вернуть итог.

    Мост вызывает `prompt.prompt({noReply: true})`: сервер создаёт сообщение,
    материализует вложения в `uploads/<session>/<message>/` и раскладывает
    части точно так же, как при реальной отправке, но не запускает генерацию.
    Возвращаются итоговые части в порядке сохранения — ровно то, что попадает
    в запрос к провайдеру.
    """
    return _run_bridge(
        session_id=session_id,
        message_id=message_id,
        work_dir=work_dir,
        parts=parts,
        agent=agent,
        model=model,
        base_dir=base_dir,
        timeout=timeout,
    )["parts"]


def resolve_parts_with_model_messages(**kwargs: Any) -> Dict[str, Any]:
    """То же, что `resolve_parts_via_server`, но с сообщениями для провайдера.

    Помимо итоговых частей возвращает `model_messages` — результат бэкендовой
    функции `toModelMessages`, то есть уже готовый пользовательский контент
    запроса к модели (склеенные текстовые части, файлы без `ignored`).
    """
    return _run_bridge(**kwargs)


def _run_bridge(
    *,
    session_id: str,
    message_id: str,
    work_dir: str,
    parts: List[Dict[str, Any]],
    agent: str = "build",
    model: Optional[Dict[str, str]] = None,
    base_dir: Optional[Path] = None,
    timeout: float = _BRIDGE_TIMEOUT,
) -> Dict[str, Any]:
    from ..core.config import ClientConfig

    cfg = ClientConfig().resolve()
    package = cfg.opencode_package()
    if not package.exists():
        raise AgentError(f"desktop-ai-app backend not found: {package}")

    work = tempfile.mkdtemp(prefix="gigatool-bridge-")
    job_path = Path(work) / "job.json"
    out_path = Path(work) / "result.json"
    job = {
        "sessionID": session_id,
        "messageID": message_id,
        "workDir": str(Path(work_dir).resolve()),
        "baseDir": str(Path(base_dir).resolve()) if base_dir else None,
        "agent": agent,
        "model": model,
        "parts": parts,
    }
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if k != "OPENCODE_BASE_DIR"}
    if base_dir:
        env["OPENCODE_BASE_DIR"] = str(Path(base_dir).resolve())
    result = subprocess.run(
        [
            _bun_binary(),
            "run",
            "--conditions=browser",
            str(bridge_dir() / "bridge.ts"),
            "--in",
            str(job_path),
            "--out",
            str(out_path),
        ],
        cwd=str(package),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    try:
        if not out_path.exists():
            raise AgentError(
                "attachment bridge failed: "
                f"exit={result.returncode} stdout={result.stdout[-2000:]} stderr={result.stderr[-2000:]}"
            )
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not payload.get("ok"):
        raise AgentError(f"attachment bridge failed: {payload.get('error')}")
    return payload


def resolve_attachments(
    message: str,
    attachment_paths: List[str],
    *,
    session_id: str,
    message_id: str,
    work_dir: str,
    agent: str = "build",
    model: Optional[Dict[str, str]] = None,
    base_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Полный путь UI: подготовка файлов -> серверная материализация -> части."""
    attachments = prepare_attachments(attachment_paths, base_dir=Path(work_dir))
    parts = build_message_parts(message, attachments)
    return resolve_parts_via_server(
        session_id=session_id,
        message_id=message_id,
        work_dir=work_dir,
        parts=parts,
        agent=agent,
        model=model,
        base_dir=base_dir,
    )


__all__ = [
    "CSV_MIME",
    "DOCX_MIME",
    "PPTX_MIME",
    "PreparedAttachment",
    "attachment_mime",
    "bridge_dir",
    "build_message_parts",
    "file_url",
    "prepare_attachment",
    "prepare_attachments",
    "resolve_attachments",
    "resolve_parts_via_server",
]