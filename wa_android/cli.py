"""Command-line entry point for the Android WhatsApp exporter."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wa_scraper.merge import merge_shards

from wa_android.export import ExportConfig, export_group
from wa_android.logutil import log

_DOC = """\
whatsapp_android_export.py  — full history from Android phone
═══════════════════════════════════════════════════════════════════════════════
Exports a WhatsApp group chat by reading the phone's local msgstore.db via ADB.
Bypasses WhatsApp Web's linked-device history cap.

Output format matches whatsapp_scraper.py (same JSON schema and shard files).

Setup
─────
  pip install pycryptodome wa-crypt-tools protobuf javaobj-py3
  Install Android platform-tools (adb) and enable USB debugging on the phone.
  In WhatsApp: Settings → Chats → Chat backup → Back up (local backup).

Usage
─────
  python whatsapp_android_export.py --group "My Group"
  python whatsapp_android_export.py --group "Team" --output team.json
  python whatsapp_android_export.py --group "Team" --backup-key <64-hex-chars>
  python whatsapp_android_export.py --group "Team" --db ~/.wa_android_cache/msgstore.db
  python whatsapp_android_export.py --pull-only
  python whatsapp_android_export.py --output team.json --merge-only

Notes
─────
  • Encrypted backups (crypt15) need the 64-char key from WhatsApp → Settings →
    Chats → Chat backup → End-to-end encrypted backup.
  • Rooted phones pull live databases directly (fastest, no decryption).
  • --legacy-backup is for Android ≤13 only; off by default and risky.
  • Message ids differ from WhatsApp Web exports (key_id vs DOM data-id).
═══════════════════════════════════════════════════════════════════════════════
"""


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export a WhatsApp Android group chat to JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_DOC,
    )
    ap.add_argument(
        "--group",
        default=None,
        help="Group name to export (partial/case-insensitive match). "
        "Required unless --merge-only or --pull-only is used.",
    )
    ap.add_argument(
        "--output",
        default="wa_chat.json",
        help="Output JSON file path (default: wa_chat.json).",
    )
    ap.add_argument(
        "--device",
        default=None,
        help="ADB device serial (auto-pick when only one device is connected).",
    )
    ap.add_argument(
        "--cache-dir",
        default=None,
        help="Local directory for pulled/decrypted databases "
        "(default: ~/.wa_android_cache/<serial>).",
    )
    ap.add_argument(
        "--pull-only",
        action="store_true",
        help="Only pull backup files from the phone; do not export messages.",
    )
    ap.add_argument(
        "--db",
        default=None,
        help="Use an existing msgstore.db (or encrypted backup) instead of ADB pull.",
    )
    ap.add_argument(
        "--wa-db",
        default=None,
        help="Path to wa.db contacts database (optional but recommended).",
    )
    ap.add_argument(
        "--backup-key",
        default=None,
        help=(
            "Decryption key: 64-char hex for crypt15, or path to the 'key' "
            "file for crypt12/crypt14 (auto-detected if placed next to the backup)."
        ),
    )
    ap.add_argument(
        "--from-date",
        default=None,
        help="Only export messages on/after this date (dd/mm/yyyy or yyyy-mm-dd).",
    )
    ap.add_argument(
        "--to-date",
        default=None,
        help="Only export messages on/before this date (dd/mm/yyyy or yyyy-mm-dd).",
    )
    ap.add_argument(
        "--shard-size",
        type=int,
        default=5000,
        help="Max messages per shard file (default: 5000).",
    )
    ap.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Do not load existing shards; start fresh.",
    )
    ap.set_defaults(resume=True)
    ap.add_argument(
        "--merge-after",
        action="store_true",
        help="After export, merge all shards into --output.",
    )
    ap.add_argument(
        "--merge-only",
        action="store_true",
        help="Skip export; merge existing shards into --output and exit.",
    )
    ap.add_argument(
        "--legacy-backup",
        action="store_true",
        help=(
            "DANGEROUS: use legacy adb backup extraction (Android ≤13 only). "
            "Off by default."
        ),
    )
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass

    if args.merge_only:
        merge_shards(args.output)
        return

    if not args.group and not args.pull_only:
        ap.error("--group is required unless --merge-only or --pull-only is used.")

    config = ExportConfig(
        group=args.group or "",
        output=args.output,
        device=args.device,
        cache_dir=args.cache_dir,
        db=args.db,
        wa_db=args.wa_db,
        backup_key=args.backup_key,
        from_date=args.from_date,
        to_date=args.to_date,
        shard_size=args.shard_size,
        resume=args.resume,
        pull_only=args.pull_only,
        legacy_backup=args.legacy_backup,
    )

    export_group(config)

    if args.merge_after and not args.pull_only:
        merge_shards(config.output)
