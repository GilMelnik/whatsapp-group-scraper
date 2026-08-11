"""Decrypt WhatsApp crypt15 backup files to plaintext SQLite."""

from __future__ import annotations

import getpass
import logging
import re
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Union

from wa_crypt_tools.lib.db.dbfactory import DatabaseFactory
from wa_crypt_tools.lib.key.keyfactory import KeyFactory

from wa_android.logutil import log

if TYPE_CHECKING:
    from wa_android.discover import DiscoveredFiles


_HEX_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class DecryptError(RuntimeError):
    pass


def _silence_wa_crypt_logs() -> None:
    logging.getLogger("wa_crypt_tools").setLevel(logging.ERROR)
    logging.getLogger("wa_crypt_tools.lib").setLevel(logging.ERROR)


def _read_key_file(path: Path) -> str:
    """Load a crypt15 hex key saved as plain text."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise DecryptError(f"Key file not found: {path}")
    text = path.read_bytes().decode("utf-8", errors="ignore").strip()
    if _HEX_KEY_RE.match(text):
        return text.lower()
    raise DecryptError(
        f"Key file {path} does not contain a 64-character hex crypt15 key."
    )


def normalize_key(key: Union[str, Path, bytes, None]) -> str:
    """Coerce a crypt15 key argument into a 64-character hex string."""
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
        "Invalid backup key. Provide a 64-character hex key (crypt15) or a "
        "path to a file containing it."
    )


def prompt_for_key() -> str:
    log(
        "Enter the 64-character backup encryption key from WhatsApp -> "
        "Settings -> Chats -> Chat backup -> End-to-end encrypted backup:"
    )
    while True:
        entered = getpass.getpass("Backup key: ").strip()
        if _HEX_KEY_RE.match(entered):
            return entered.lower()
        path = Path(entered).expanduser()
        if path.is_file():
            return _read_key_file(path)
        log("Enter a 64-character hex key (crypt15) or a path to a key file.")


def decrypt_backup(
    encrypted_path: Path,
    output_path: Path,
    key: Union[str, Path, bytes, None],
) -> Path:
    """Decrypt a crypt15 WhatsApp backup into a plaintext SQLite database."""
    encrypted_path = encrypted_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not encrypted_path.is_file():
        raise DecryptError(f"Encrypted backup not found: {encrypted_path}")

    resolved_key = key
    if resolved_key is None:
        same_dir = encrypted_path.parent
        for name in ("encrypted_backup.key", "key"):
            candidate = same_dir / name
            if candidate.is_file():
                resolved_key = _read_key_file(candidate)
                log(f"Using key file {candidate}")
                break
    if resolved_key is None:
        resolved_key = prompt_for_key()
    else:
        resolved_key = normalize_key(resolved_key)

    _silence_wa_crypt_logs()
    try:
        key_obj = KeyFactory.new(resolved_key)
    except Exception as exc:
        raise DecryptError(f"Could not load decryption key: {exc}") from exc

    with encrypted_path.open("rb") as encrypted:
        try:
            db = DatabaseFactory.from_file(encrypted)
        except Exception as exc:
            raise DecryptError(f"Could not parse backup header: {exc}") from exc
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
    log(f"Decrypted database -> {output_path}")
    return output_path


def ensure_plaintext_db(
    db_path: Path,
    cache_dir: Path,
    output_name: str,
    backup_key: Union[str, Path, None],
) -> Path:
    """Return a plaintext SQLite path, decrypting a crypt15 backup when needed."""
    db_path = db_path.expanduser().resolve()
    if ".crypt" not in db_path.name.lower():
        return db_path
    out = cache_dir / output_name
    decrypt_backup(db_path, out, backup_key)
    return out


def ensure_plaintext_msgstore(
    discovered: DiscoveredFiles,
    cache_dir: Path,
    backup_key: Union[str, Path, None],
) -> Path:
    """Return a plaintext msgstore.db path, decrypting the crypt15 backup if needed."""
    msgstore = discovered.msgstore
    if not discovered.encrypted:
        return msgstore
    key: Union[str, Path, None] = backup_key
    if key is None and discovered.key_file is not None:
        key = _read_key_file(discovered.key_file)
    return ensure_plaintext_db(msgstore, cache_dir, "msgstore.db", key)
