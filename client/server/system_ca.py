"""Сводный CA-бандл для sidecar'а: корни, которым доверяет система и desktop.

Node проверяет TLS по встроенному списку Mozilla плюс `NODE_EXTRA_CA_CERTS`.
Этого хватает публичным хостам, но не тестовым стендам банка: их листовой
сертификат выпущен внутренним `SberCA Test Ext G2`, которого нет ни в Mozilla,
ни в `/etc/ssl/certs`. В desktop проверку выполняет Chromium/Electron, который
доверяет NSS-хранилищу пользователя (`~/.pki/nssdb`) и на dev-канале вообще
игнорирует ошибки сертификата для корпоративных хостов (`app.ts`,
`certificate-error`; `apiClient.ts`, `rejectUnauthorized: false`). Голому Node
такого поведения никто не даёт, поэтому корни надо отдать явно.

`build_bundle()` собирает один PEM из всего, что машина уже считает доверенным:
NSS-анкоры с флагом `C`/`T` (как Chromium), `/etc/ssl/certs`, бандл CA рантайма
GigaCode и уже выставленные `NODE_EXTRA_CA_CERTS`/`SSL_CERT_FILE`. Проверка
сертификата остаётся включённой — расширяется только список доверенных корней.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional

# Файловое хранилище корней ОС (Debian/Ubuntu).
_SYSTEM_BUNDLE = Path("/etc/ssl/certs/ca-certificates.crt")

# NSS-хранилище пользователя — источник анкоров для Chromium/Electron.
_NSS_DB = Path.home() / ".pki" / "nssdb"

# CA-бандл рантайма GigaCode: содержит корни тестовых стендов, которые CLI
# выставляет через SSL_CERT_FILE. Нужен, когда harness запускают из обычного
# терминала, где этой переменной нет.
_RUNTIME_CA_DIR = Path.home() / ".gigacode" / ".protected-storage" / "certs"

# Переменные, через которые окружение уже отдаёт дополнительные корни.
_ENV_CA_NAMES = ("NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE")

_BEGIN = "-----BEGIN CERTIFICATE-----"
_END = "-----END CERTIFICATE-----"


def _pem_blocks(text: str) -> List[str]:
    """Вырезать все PEM-сертификаты из текста (по порядку, без дублей в тексте)."""
    blocks: List[str] = []
    pos = 0
    while True:
        start = text.find(_BEGIN, pos)
        if start < 0:
            return blocks
        stop = text.find(_END, start)
        if stop < 0:
            return blocks
        stop += len(_END)
        blocks.append(text[start:stop].strip() + "\n")
        pos = stop


def _fingerprint(pem: str) -> str:
    body = "".join(line for line in pem.splitlines() if not line.startswith("-----"))
    return hashlib.sha256(body.encode("ascii", "ignore")).hexdigest()


def _run(cmd: List[str]) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def trusted_nicknames(listing: str) -> List[str]:
    """Ники из вывода `certutil -L` с доверием `C` (CA) или `T` (trusted).

    Строка таблицы выглядит как `<ник может содержать пробелы>  C,,`, поэтому
    атрибуты доверия — последний токен. Личный сертификат без доверия (` ,, `)
    в бандл не попадает: доверенным корнем он не является.
    """
    names: List[str] = []
    for line in listing.splitlines():
        parts = line.rsplit(None, 1)
        if len(parts) != 2:
            continue
        nick, trust = parts[0].strip(), parts[1].strip()
        if not nick or not trust or trust[0] not in "CT":
            continue
        names.append(nick)
    return names


def nss_anchors(db_dir: Path = _NSS_DB) -> List[str]:
    """Доверенные анкоры из NSS-хранилища; пусто, если certutil недоступен."""
    if not (db_dir / "cert9.db").exists():
        return []
    db = f"sql:{db_dir}"
    pems: List[str] = []
    for nick in trusted_nicknames(_run(["certutil", "-L", "-d", db])):
        pems.extend(_pem_blocks(_run(["certutil", "-L", "-d", db, "-n", nick, "-a"])))
    return pems


def sources() -> List[Path]:
    """Файлы с корнями: система, бандл рантайма, уже выставленные env-файлы."""
    files = [_SYSTEM_BUNDLE]
    files.extend(sorted(_RUNTIME_CA_DIR.glob("runtime-ca-*.pem")))
    for name in _ENV_CA_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            files.append(Path(value))
    return files


def build_bundle(dest: Path) -> Optional[Path]:
    """Собрать один PEM с корнями для `NODE_EXTRA_CA_CERTS`.

    Возвращает `dest` (или `None`, если корней не нашлось). Дубликаты
    отбрасываются по отпечатку: NSS, `/etc/ssl/certs` и бандл рантайма
    пересекаются.
    """
    seen = set()
    chunks: List[str] = []
    candidates: Iterable[str] = list(nss_anchors())
    for pem in candidates:
        fp = _fingerprint(pem)
        if fp in seen:
            continue
        seen.add(fp)
        chunks.append(pem)
    for path in sources():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pem in _pem_blocks(text):
            fp = _fingerprint(pem)
            if fp in seen:
                continue
            seen.add(fp)
            chunks.append(pem)
    if not chunks:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(chunks), encoding="utf-8")
    return dest


__all__ = ["build_bundle", "nss_anchors", "sources", "trusted_nicknames"]