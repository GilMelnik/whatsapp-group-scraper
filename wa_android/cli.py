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
  (a plain, already decrypted msgstore.db is accepted too)

Usage
─────
  python whatsapp_android_export.py \\
      --db msgstore.db.crypt15 \\
      --group "My Group" --backup-key <64-hex> \\
      --members members.json --output chat_android.json

Output
──────
  A JSON list of messages in the database's order (time, then row id; never
  re-sorted). "time" is HH:MM:SS. A sender (also a quoted sender, a reactor and a
  tagged member) is its member id: the member's phone id when the database knows
  it, else its lid id, the same throughout the chat history; your own are "You".
  "mentions" lists the tags of the text: [{"tag": "<digits>", "member": "<id>"}].
  --members keeps the ids constant across backups (ids only, no names).
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
        help=(
            "Not used any more (senders are member ids from msgstore.db); accepted so "
            "older command lines still run."
        ),
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
    ap.add_argument(
        "--members",
        default=None,
        help=(
            "Members file (JSON, ids only): read when it exists and written back "
            "extended, so every member keeps one id across backups. Without it the "
            "ids come from this backup alone."
        ),
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
        backup_key=args.backup_key,
        from_date=args.from_date,
        to_date=args.to_date,
        members=args.members,
    )
    export_group(config)
