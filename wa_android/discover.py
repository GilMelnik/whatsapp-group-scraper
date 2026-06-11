"""Locate WhatsApp database files on a device or local cache."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from wa_android import adb
from wa_android.logutil import log


REMOTE_DB_DIRS = [
    "/sdcard/Android/media/com.whatsapp/WhatsApp/Databases",
    "/sdcard/Android/media/com.whatsapp.w4b/WhatsApp/Databases",
    "/sdcard/WhatsApp/Databases",
    "/storage/emulated/0/Android/media/com.whatsapp/WhatsApp/Databases",
    "/storage/emulated/0/WhatsApp/Databases",
]

KEY_REMOTE_PATHS = [
    "/sdcard/Android/media/com.whatsapp/WhatsApp/Databases/.nomedia",
    "/sdcard/WhatsApp/Databases/.nomedia",
    "/data/data/com.whatsapp/files/key",
    "/data/data/com.whatsapp/files/encrypted_backup.key",
]

MSGSTORE_RE = re.compile(
    r"^msgstore(?:-\d{4}-\d{2}-\d{2})?\.db(?:\.crypt(?:12|14|15))?$",
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
    match = re.search(r"\.crypt(12|14|15)$", name, re.I)
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
        [p for p in directory.iterdir() if re.match(r"^wa\.db(?:\.crypt(?:12|14|15))?$", p.name, re.I)]
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


def pull_from_device(
    serial: str,
    cache_dir: Path,
    *,
    try_root: bool = True,
) -> DiscoveredFiles:
    cache_dir.mkdir(parents=True, exist_ok=True)

    if try_root and adb.is_rooted(serial):
        log("✓  Rooted device detected — pulling live databases …")
        msgstore, wa_db = adb.pull_root_databases(serial, cache_dir)
        return DiscoveredFiles(
            msgstore=msgstore,
            wa_db=wa_db if wa_db.is_file() else None,
            encrypted=False,
        )

    pulled_msgstores: List[Path] = []
    for remote_dir in REMOTE_DB_DIRS:
        if not adb.remote_exists(serial, remote_dir):
            continue
        names = adb.remote_glob(serial, f"{remote_dir}/msgstore*")
        for name in names:
            if not MSGSTORE_RE.match(Path(name).name):
                continue
            local = cache_dir / Path(name).name
            log(f"↓  Pulling {name} …")
            adb.pull(serial, name, local)
            pulled_msgstores.append(local)

    msgstore = _pick_newest(pulled_msgstores)
    if msgstore is None:
        raise FileNotFoundError(
            "No WhatsApp backup found on the phone. Enable local backup in "
            "WhatsApp → Settings → Chats → Chat backup → Back up, then retry."
        )

    wa_db = None
    for remote_dir in REMOTE_DB_DIRS:
        remote_wa = f"{remote_dir}/wa.db"
        if adb.remote_exists(serial, remote_wa):
            wa_db = cache_dir / "wa.db"
            log(f"↓  Pulling {remote_wa} …")
            adb.pull(serial, remote_wa, wa_db)
            break

    key_file = None
    for remote_key in KEY_REMOTE_PATHS:
        if not adb.remote_exists(serial, remote_key):
            continue
        name = Path(remote_key).name
        if name == ".nomedia":
            continue
        local = cache_dir / name
        try:
            if remote_key.startswith("/data/"):
                if not adb.is_rooted(serial):
                    continue
                tmp = f"/sdcard/wa_export_{name}"
                adb.shell(serial, f"su -c 'cp {remote_key} {tmp} && chmod 644 {tmp}'")
                adb.pull(serial, tmp, local)
                adb.shell(serial, f"rm -f {tmp}")
            else:
                adb.pull(serial, remote_key, local)
            key_file = local
            break
        except adb.AdbError:
            continue

    crypt = _crypt_version(msgstore.name)
    return DiscoveredFiles(
        msgstore=msgstore,
        wa_db=wa_db if wa_db and wa_db.is_file() else None,
        key_file=key_file,
        encrypted=crypt is not None,
        crypt_version=crypt,
    )


def resolve_databases(
    *,
    serial: Optional[str],
    cache_dir: Optional[Path],
    db_path: Optional[str],
    wa_db_path: Optional[str],
    pull_only: bool = False,
) -> Tuple[DiscoveredFiles, Path]:
    """Return discovered files and the working cache directory."""
    if db_path:
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
            wa = None
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
        else:
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

    device = adb.pick_device(serial)
    dest = cache_dir or adb.default_cache_dir(device.serial)
    discovered = pull_from_device(device.serial, dest)
    if pull_only:
        log(f"✓  Pulled files to {dest}")
    return discovered, dest
