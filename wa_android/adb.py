"""ADB device detection and file transfer helpers."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from wa_android.logutil import log


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class Device:
    serial: str
    state: str


def _run_adb(args: List[str], *, serial: Optional[str] = None) -> str:
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise AdbError(
            "adb not found on PATH. Install Android platform-tools and ensure "
            "'adb' is available."
        ) from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout or "").strip()
        raise AdbError(stderr or f"adb {' '.join(args)} failed")
    return (proc.stdout or "").strip()


def list_devices() -> List[Device]:
    out = _run_adb(["devices", "-l"])
    devices: List[Device] = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if state == "unauthorized":
            raise AdbError(
                f"Device {serial} is unauthorized. Accept the USB debugging "
                "prompt on the phone."
            )
        if state in {"device", "offline"}:
            devices.append(Device(serial=serial, state=state))
    return [d for d in devices if d.state == "device"]


def pick_device(serial: Optional[str] = None) -> Device:
    devices = list_devices()
    if not devices:
        raise AdbError(
            "No Android device detected. Connect the phone via USB (or wireless "
            "ADB), enable USB debugging, and run: adb devices"
        )
    if serial:
        for dev in devices:
            if dev.serial == serial:
                return dev
        known = ", ".join(d.serial for d in devices)
        raise AdbError(f"Device {serial!r} not found. Connected: {known}")
    if len(devices) > 1:
        known = ", ".join(d.serial for d in devices)
        raise AdbError(
            f"Multiple devices connected ({known}). Pass --device SERIAL."
        )
    return devices[0]


def shell(serial: str, command: str) -> str:
    return _run_adb(["shell", command], serial=serial)


def pull(serial: str, remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    _run_adb(["pull", remote_path, str(local_path)], serial=serial)


def remote_exists(serial: str, remote_path: str) -> bool:
    out = shell(serial, f"ls {remote_path} 2>/dev/null")
    return bool(out) and "No such file" not in out


def remote_glob(serial: str, pattern: str) -> List[str]:
    """Return remote paths matching a glob (best-effort)."""
    out = shell(serial, f"ls -1 {pattern} 2>/dev/null || true")
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def android_api_level(serial: str) -> Optional[int]:
    out = shell(serial, "getprop ro.build.version.sdk")
    try:
        return int(out.strip())
    except ValueError:
        return None


def is_rooted(serial: str) -> bool:
    out = shell(serial, "su -c id 2>/dev/null || id")
    return "uid=0" in out


def pull_root_databases(serial: str, dest_dir: Path) -> tuple[Path, Path]:
    """Pull plaintext msgstore.db and wa.db from a rooted device."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    msgstore_remote = "/data/data/com.whatsapp/databases/msgstore.db"
    wa_remote = "/data/data/com.whatsapp/databases/wa.db"
    msgstore_local = dest_dir / "msgstore.db"
    wa_local = dest_dir / "wa.db"
    for remote, local in ((msgstore_remote, msgstore_local), (wa_remote, wa_local)):
        tmp = f"/sdcard/wa_export_{local.name}"
        shell(serial, f"su -c 'cp {remote} {tmp} && chmod 644 {tmp}'")
        pull(serial, tmp, local)
        shell(serial, f"rm -f {tmp}")
    if not msgstore_local.is_file():
        raise AdbError("Root pull failed: msgstore.db not found.")
    return msgstore_local, wa_local


def default_cache_dir(serial: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", serial)
    return Path.home() / ".wa_android_cache" / safe
