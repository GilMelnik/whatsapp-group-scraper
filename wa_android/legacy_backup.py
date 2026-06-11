"""Optional legacy adb-backup extraction for Android ≤13 (high risk)."""

from __future__ import annotations

import io
import subprocess
import tarfile
import zlib
from pathlib import Path
from typing import Optional, Tuple

from wa_android import adb
from wa_android.discover import DiscoveredFiles, discover_local
from wa_android.logutil import log


WARNINGS = """
═══════════════════════════════════════════════════════════════════════════════
  LEGACY ADB BACKUP — USE WITH EXTREME CAUTION
═══════════════════════════════════════════════════════════════════════════════
  • Works only on Android 13 and lower (not Android 14+).
  • Requires temporarily interacting with WhatsApp's legacy backup API.
  • ALWAYS create an official WhatsApp backup first (Settings → Chats → Chat backup).
  • This path is OFF by default; you explicitly enabled --legacy-backup.
═══════════════════════════════════════════════════════════════════════════════
"""


def _extract_android_backup(ab_path: Path, dest_dir: Path) -> None:
    """Extract files from an Android .ab backup archive."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with ab_path.open("rb") as fh:
        header = fh.read(24)
        if not header.startswith(b"ANDROID BACKUP"):
            raise RuntimeError(f"Not a valid Android backup file: {ab_path}")
        fh.read(1)  # version
        compressed = fh.read(1) == b"\x01"
        payload = fh.read()
    if compressed:
        payload = zlib.decompress(payload)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tar:
        tar.extractall(path=dest_dir)


def run_legacy_backup(
    *,
    serial: Optional[str],
    cache_dir: Optional[Path],
) -> Tuple[DiscoveredFiles, Path]:
    """Run adb backup and extract msgstore.db + key from the archive."""
    log(WARNINGS)
    device = adb.pick_device(serial)
    api = adb.android_api_level(device.serial)
    if api is not None and api >= 34:
        raise RuntimeError(
            f"Legacy adb backup is not supported on API {api} (Android 14+). "
            "Use the default backup pull flow instead."
        )

    dest = cache_dir or adb.default_cache_dir(device.serial)
    dest.mkdir(parents=True, exist_ok=True)
    ab_file = dest / "whatsapp_legacy.ab"

    log(
        "When the phone shows 'Back up my data', tap it (leave password blank "
        "unless you set one on the PC prompt)."
    )
    cmd = [
        "adb",
        "-s",
        device.serial,
        "backup",
        "-noapk",
        "-f",
        str(ab_file),
        "com.whatsapp",
    ]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("adb not found on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"adb backup failed: {exc}") from exc

    if not ab_file.is_file() or ab_file.stat().st_size < 100:
        raise RuntimeError(
            "adb backup produced no data. Ensure the phone screen is unlocked "
            "and you confirmed the backup prompt."
        )

    extract_dir = dest / "legacy_extract"
    if extract_dir.exists():
        for child in extract_dir.iterdir():
            if child.is_file():
                child.unlink()
    else:
        extract_dir.mkdir(parents=True)

    log("↺  Extracting legacy backup archive …")
    _extract_android_backup(ab_file, extract_dir)

    for name in ("msgstore.db", "key", "wa.db"):
        found = list(extract_dir.rglob(name))
        if found:
            target = dest / name
            target.write_bytes(found[0].read_bytes())
            log(f"✓  Extracted {name}")

    discovered = discover_local(dest)
    log(f"✓  Legacy backup extracted to {dest}")
    return discovered, dest
