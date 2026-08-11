"""Locate crypt15 WhatsApp database files on disk (file input only)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple


MSGSTORE_RE = re.compile(
    r"^msgstore(?:-\d{4}-\d{2}-\d{2})?\.db(?:\.crypt15)?$",
    re.I,
)


@dataclass(frozen=True)
class DiscoveredFiles:
    msgstore: Path
    wa_db: Optional[Path] = None
    key_file: Optional[Path] = None
    encrypted: bool = False
    crypt_version: Optional[str] = None


def _parse_backup_date(name: str) -> datetime:
    match = re.search(r"msgstore-(\d{4}-\d{2}-\d{2})", name, re.I)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d")
        except ValueError:
            pass
    return datetime.min


def _crypt_version(name: str) -> Optional[str]:
    match = re.search(r"\.crypt(15)$", name, re.I)
    return match.group(1) if match else None


def _pick_newest(paths: List[Path]) -> Optional[Path]:
    candidates = [p for p in paths if p.is_file() and MSGSTORE_RE.match(p.name)]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda p: (_parse_backup_date(p.name), p.stat().st_mtime, p.name),
    )


def discover_local(directory: Path) -> DiscoveredFiles:
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    msgstore = _pick_newest(list(directory.glob("msgstore*.db*")))
    if msgstore is None:
        plain = directory / "msgstore.db"
        if plain.is_file():
            msgstore = plain
    if msgstore is None:
        raise FileNotFoundError(
            f"No msgstore backup found in {directory}. Expected msgstore.db or "
            "msgstore-*.db.crypt15."
        )

    wa_db = _pick_newest(
        [
            p
            for p in directory.iterdir()
            if re.match(r"^wa\.db(?:\.crypt15)?$", p.name, re.I)
        ]
    )
    if wa_db is None:
        plain_wa = directory / "wa.db"
        wa_db = plain_wa if plain_wa.is_file() else None

    key_file = None
    for name in ("encrypted_backup.key", "key"):
        candidate = directory / name
        if candidate.is_file():
            key_file = candidate
            break

    crypt = _crypt_version(msgstore.name)
    return DiscoveredFiles(
        msgstore=msgstore,
        wa_db=wa_db,
        key_file=key_file,
        encrypted=crypt is not None,
        crypt_version=crypt,
    )


def resolve_databases(
    *,
    db_path: str,
    wa_db_path: Optional[str],
) -> Tuple[DiscoveredFiles, Path]:
    """Resolve a copied crypt15 backup (a file or a folder) into DiscoveredFiles.

    Returns the discovered files and the working directory (used as the scratch
    location for decrypted plaintext databases).
    """
    db_file = Path(db_path).expanduser().resolve()
    if db_file.is_file():
        base = db_file.parent
        crypt = _crypt_version(db_file.name)
        key_file = None
        for name in ("encrypted_backup.key", "key"):
            candidate = base / name
            if candidate.is_file():
                key_file = candidate
                break
        if wa_db_path:
            wa_candidate = Path(wa_db_path).expanduser().resolve()
            wa = wa_candidate if wa_candidate.is_file() else None
        else:
            default_wa = base / "wa.db"
            wa = default_wa if default_wa.is_file() else None
        discovered = DiscoveredFiles(
            msgstore=db_file,
            wa_db=wa,
            key_file=key_file,
            encrypted=crypt is not None,
            crypt_version=crypt,
        )
        return discovered, base

    base = db_file
    discovered = discover_local(base)
    if wa_db_path:
        wa = Path(wa_db_path).expanduser().resolve()
        discovered = DiscoveredFiles(
            msgstore=discovered.msgstore,
            wa_db=wa if wa.is_file() else discovered.wa_db,
            key_file=discovered.key_file,
            encrypted=discovered.encrypted,
            crypt_version=discovered.crypt_version,
        )
    return discovered, base
