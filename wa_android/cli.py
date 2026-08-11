"""Command-line entry point for the Android WhatsApp crypt15 exporter."""

from __future__ import annotations

import argparse
import sys

from wa_android.export import ExportConfig, export_group

_DOC = """\
whatsapp_android_export.py  — export a WhatsApp group from a crypt15 backup
═══════════════════════════════════════════════════════════════════════════════
Decrypts a copied WhatsApp crypt15 backup (msgstore.db.crypt15) and reads one
group chat into a single chat_android.json.

Setup
─────
  pip install -r requirements.txt

On the phone (once):
  WhatsApp → Settings → Chats → Chat backup → turn on
  "End-to-end encrypted backup" (this produces msgstore.db.crypt15), then
  "Back up". Save the 64-character hex key it shows you.

Copy off the phone (USB/MTP or a file manager):
  Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15
  (optionally wa.db for readable contact names)

Usage
─────
  python whatsapp_android_export.py \\
      --db msgstore.db.crypt15 --wa-db wa.db \\
      --group "My Group" --backup-key <64-hex> \\
      --output chat_android.json
═══════════════════════════════════════════════════════════════════════════════
"""


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export a WhatsApp group chat from a crypt15 backup to JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_DOC,
    )
    ap.add_argument(
        "--db",
        required=True,
        help="Path to the copied msgstore.db.crypt15 (or a folder containing it).",
    )
    ap.add_argument(
        "--group",
        required=True,
        help="Group name to export (partial, case-insensitive match).",
    )
    ap.add_argument(
        "--output",
        default="chat_android.json",
        help="Output JSON file path (default: chat_android.json).",
    )
    ap.add_argument(
        "--wa-db",
        default=None,
        help="Path to wa.db contacts database (optional, for readable names).",
    )
    ap.add_argument(
        "--backup-key",
        default=None,
        help=(
            "crypt15 decryption key: 64-char hex, or a path to a file containing "
            "it (auto-detected if a 'key' file sits next to the backup; otherwise "
            "you are prompted)."
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
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass

    config = ExportConfig(
        group=args.group,
        db=args.db,
        output=args.output,
        wa_db=args.wa_db,
        backup_key=args.backup_key,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    export_group(config)
