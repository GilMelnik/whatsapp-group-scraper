"""Decrypt WhatsApp crypt12/14/15 backup files to plaintext SQLite."""

from __future__ import annotations

import getpass
import logging
import re
import zlib
from pathlib import Path
from typing import Optional, Union

from wa_android.logutil import log

try:
    from Cryptodome.Cipher import AES
except ImportError:
    from Crypto.Cipher import AES  # type: ignore

from wa_crypt_tools.lib.db.dbfactory import DatabaseFactory
from wa_crypt_tools.lib.key.keyfactory import KeyFactory


_HEX_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class DecryptError(RuntimeError):
    pass


def _silence_wa_crypt_logs() -> None:
    logging.getLogger("wa_crypt_tools").setLevel(logging.ERROR)
    logging.getLogger("wa_crypt_tools.lib").setLevel(logging.ERROR)


def _read_key_file(path: Path) -> Union[str, Path]:
    """Load a crypt15 hex key saved as plain text, or a binary key file."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise DecryptError(f"Key file not found: {path}")
    data = path.read_bytes()
    if len(data) <= 128:
        text = data.decode("utf-8", errors="ignore").strip()
        if _HEX_KEY_RE.match(text):
            return text.lower()
    return path


def normalize_key(key: Union[str, Path, bytes, None]) -> Union[str, Path]:
    if key is None:
        raise DecryptError("No decryption key provided.")
    if isinstance(key, Path):
        return _read_key_file(key)
    if isinstance(key, bytes):
        if len(key) == 32:
            return key.hex()
        raise DecryptError("Key bytes must be 32 bytes (crypt15).")
    text = str(key).strip()
    if _HEX_KEY_RE.match(text):
        return text.lower()
    path = Path(text).expanduser()
    if path.is_file():
        return _read_key_file(path)
    raise DecryptError(
        "Invalid backup key. Provide a 64-character hex key or a key file "
        "(encrypted_backup.key / key)."
    )


def prompt_for_key(crypt_version: Optional[str] = None) -> Union[str, Path]:
    if crypt_version in {"12", "14"}:
        log(
            "Crypt14 needs the WhatsApp key file (usually named 'key', 131 bytes).\n"
            "  Copy it from the phone into the same folder as the backup, e.g.:\n"
            "    outputs/from_phone/key\n"
            "  Then enter the path to that file (or press Enter if it is already there):"
        )
        while True:
            entered = input("Key file path: ").strip()
            if not entered:
                for name in ("key", "encrypted_backup.key"):
                    candidate = Path(name)
                    if candidate.is_file():
                        return candidate
                log("No key file found in the current directory. Enter a path.")
                continue
            path = Path(entered).expanduser()
            if path.is_file():
                return path
            log(f"File not found: {path}")
        raise DecryptError("No key file provided.")  # unreachable

    log(
        "Enter the 64-character backup encryption key from WhatsApp → "
        "Settings → Chats → Chat backup → End-to-end encrypted backup:"
    )
    while True:
        entered = getpass.getpass("Backup key: ").strip()
        if _HEX_KEY_RE.match(entered):
            return entered.lower()
        path = Path(entered).expanduser()
        if path.is_file():
            return path
        log(
            "Enter a 64-character hex key (crypt15) or a path to a key file."
        )


def decrypt_backup(
    encrypted_path: Path,
    output_path: Path,
    key: Union[str, Path, bytes, None],
    *,
    key_file: Optional[Path] = None,
) -> Path:
    """Decrypt an encrypted WhatsApp backup to a SQLite database."""
    encrypted_path = encrypted_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not encrypted_path.is_file():
        raise DecryptError(f"Encrypted backup not found: {encrypted_path}")

    crypt_version = None
    name = encrypted_path.name.lower()
    if ".crypt" in name:
        match = re.search(r"\.crypt(\d+)", name)
        if match:
            crypt_version = match.group(1)

    resolved_key = key_file or key
    if resolved_key is None:
        same_dir = encrypted_path.parent
        for name in ("key", "encrypted_backup.key"):
            candidate = same_dir / name
            if candidate.is_file():
                resolved_key = _read_key_file(candidate)
                log(f"✓  Using key file {candidate}")
                break
    if resolved_key is None:
        resolved_key = prompt_for_key(crypt_version)
    else:
        resolved_key = normalize_key(resolved_key)

    _silence_wa_crypt_logs()
    try:
        key_obj = KeyFactory.new(Path(resolved_key) if isinstance(resolved_key, Path) else resolved_key)
    except Exception as exc:
        raise DecryptError(f"Could not load decryption key: {exc}") from exc

    with encrypted_path.open("rb") as encrypted:
        try:
            db = DatabaseFactory.from_file(encrypted)
        except Exception as exc:
            raise DecryptError(f"Could not parse backup header: {exc}") from exc
        cipher = AES.new(key_obj.get(), AES.MODE_GCM, db.get_iv())
        ciphertext = encrypted.read()
        try:
            decrypted = db.decrypt(key_obj, ciphertext)
        except Exception as exc:
            raise DecryptError(
                "Decryption failed — the key probably does not match this backup."
            ) from exc

    try:
        plaintext = zlib.decompress(decrypted)
    except zlib.error:
        if decrypted[:15] == b"SQLite format 3":
            plaintext = decrypted
        else:
            raise DecryptError(
                "Decrypted data is not a valid SQLite database. Check the key."
            )

    if plaintext[:15] != b"SQLite format 3":
        raise DecryptError("Decrypted output is not a SQLite database.")

    output_path.write_bytes(plaintext)
    log(f"✓  Decrypted database → {output_path}")
    return output_path


def ensure_plaintext_db(
    db_path: Path,
    cache_dir: Path,
    output_name: str,
    backup_key: Union[str, Path, None],
    key_file: Optional[Path] = None,
) -> Path:
    """Return plaintext SQLite path, decrypting crypt backups when needed."""
    db_path = db_path.expanduser().resolve()
    if ".crypt" not in db_path.name.lower():
        return db_path
    out = cache_dir / output_name
    decrypt_backup(db_path, out, backup_key, key_file=key_file)
    return out


def ensure_plaintext_msgstore(
    discovered,
    cache_dir: Path,
    backup_key: Union[str, Path, None],
) -> Path:
    """Return a plaintext msgstore.db path, decrypting if needed."""
    msgstore = discovered.msgstore
    if not discovered.encrypted:
        if msgstore.name == "msgstore.db" or not str(msgstore).endswith(".crypt"):
            return msgstore
        raise DecryptError(f"Unrecognized database file: {msgstore}")

    key = backup_key or discovered.key_file
    if key is None and discovered.key_file is None:
        same_dir = msgstore.parent
        for name in ("key", "encrypted_backup.key"):
            candidate = same_dir / name
            if candidate.is_file():
                key = _read_key_file(candidate)
                log(f"✓  Found key file {candidate}")
                break
    return ensure_plaintext_db(
        msgstore, cache_dir, "msgstore.db", key, key_file=discovered.key_file
    )
