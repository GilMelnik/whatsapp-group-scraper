"""Command-line entry point for the WhatsApp group scraper."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wa_scraper.merge import merge_shards
from wa_scraper.scraper import ScraperConfig, WhatsAppScraper
from wa_scraper.utils import log

_DOC = """\
whatsapp_scraper.py  — Hebrew + English aware
═══════════════════════════════════════════════════════════════════════════════
Scrapes a WhatsApp Web group chat and exports every message as JSON.
Handles messages and nicknames in Hebrew, English, or mixed RTL+LTR text.

Output format  (wa_chat.json):
────────────────────────────────
[
  {
    "id":          "false_12345678901@g.us_ABCDEF0123",
    "date":        "07/06/2025",
    "time":        "14:32",
    "sender":      { "user_name": "Alice", "phone": null },
    "text":        "See you tomorrow!",
    "is_outgoing": false,
    "quote": {
      "sender": { "user_name": "Bob", "phone": null },
      "text":   "Are you coming?"
    },
    "reactions": [
      { "emoji": "👍", "count": 2, "senders": [
          { "user_name": "Bob", "phone": null },
          { "user_name": "Carol", "phone": "+1 555-0100" }
        ] }
    ],
    "media_type": null,
    "is_forwarded": false,
    "is_deleted": false,
    "link_preview": null
  },
  ...
]

Captured fields
───────────────
  • sender       – { user_name, phone } for the message author
                   ("You" for outgoing messages; phone-only when unsaved)
  • date         – calendar date from the chat's date-separator cards
  • time         – HH:MM (or H:MM AM/PM, matching your locale)
  • text         – raw message text (the caption for captioned media;
                   empty for media-only and for deleted messages)
  • is_outgoing  – true if YOU sent the message
  • quote        – { sender: { user_name, phone }, text } or null
  • reactions    – list of { emoji, count, senders[] }
                   senders lists who reacted as { user_name, phone }
                   count is always len(senders)
  • media_type   – "image" | "video" | "audio" | "sticker" | "document"
                   "gif" | "location" | null   (set even when a caption
                   is present)
  • is_forwarded – true if the message is a forwarded message
  • is_deleted   – true if the message was deleted/recalled (text is empty)
  • link_preview – { url, title, description } for an attached link
                   preview, or null

Setup
─────
  pip install selenium undetected-chromedriver

Usage
─────
  python whatsapp_scraper.py --group "My Group"
  python whatsapp_scraper.py --group "Team Chat" --output team.json
  python whatsapp_scraper.py --group "Team Chat" --scroll-pause 1.5
  python whatsapp_scraper.py --group "Team Chat" --scroll-to-chat-start
  python whatsapp_scraper.py --group "Team Chat" --from-date 01/01/2025
  python whatsapp_scraper.py --group "Team Chat" --to-date 30/06/2025
  python whatsapp_scraper.py --output team.json --merge-only

Notes
─────
  • A persistent Chrome profile is stored at ~/.wa_scraper_profile so the QR
    scan is only required once.
  • The browser window is kept visible (not headless) and all interactions use
    human-like timing and random-step scrolling.
  • WhatsApp Web uses a virtual list – rows outside the viewport are removed
    from the DOM, so messages are harvested progressively as you scroll.
  • Reactor names are collected by opening the reactions drawer.
  • Scrolling harvests from the newest messages upward, anchored on the last
    scraped row so rows are not skipped.
  • Use --scroll-to-chat-start to reach the very beginning of a chat; the
    scraper clicks WhatsApp's "load older messages from your phone" card when
    it appears and keeps harvesting after the sync finishes.
  • Messages are streamed to crash-safe shard files (<stem>.partNNN<suffix>)
    that are flushed periodically and on error/interrupt, so no data is lost.
    Each shard is capped at --shard-size messages. Re-running resumes by
    default (already-captured ids are skipped); use --no-resume to disable.
  • Restart a fallen run "by date": pass --to-date with the oldest date you
    already captured (and optionally --from-date) to scrape only that window.
  • Use --merge-after (after a run) or --merge-only to combine all shards into
    a single, globally-sorted file at --output.
═══════════════════════════════════════════════════════════════════════════════
"""


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export a WhatsApp Web group chat to JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_DOC,
    )
    ap.add_argument(
        "--group",
        default=None,
        help="Group name to scrape (partial/case-insensitive match). "
        "Required unless --merge-only is used.",
    )
    ap.add_argument(
        "--output",
        default="wa_chat.json",
        help="Output JSON file path (default: wa_chat.json).",
    )
    ap.add_argument(
        "--profile-dir",
        default=None,
        help="Custom Chrome profile directory (default: ~/.wa_scraper_profile).",
    )
    ap.add_argument(
        "--scroll-pause",
        type=float,
        default=1.0,
        help="Base delay (seconds) between scroll steps (default: 1.0).",
    )
    ap.add_argument(
        "--max-top-scrolls",
        type=int,
        default=6000,
        help="Max upward scroll steps while harvesting history (default: 6000).",
    )
    ap.add_argument(
        "--scroll-to-chat-start",
        action="store_true",
        help=(
            "Keep scrolling to the very start of the chat. Clicks WhatsApp's "
            "'load older messages from your phone' prompt when it appears."
        ),
    )
    ap.add_argument(
        "--shard-size",
        type=int,
        default=5000,
        help=(
            "Max messages per output shard file before a new one is started "
            "(default: 5000). Shards are named <stem>.partNNN<suffix>."
        ),
    )
    ap.add_argument(
        "--from-date",
        default=None,
        help=(
            "Only capture messages on/after this date (dd/mm/yyyy or "
            "yyyy-mm-dd). Scrolling stops once older messages are reached."
        ),
    )
    ap.add_argument(
        "--to-date",
        default=None,
        help=(
            "Only capture messages on/before this date (dd/mm/yyyy or "
            "yyyy-mm-dd). Useful to restart a fallen run from where it stopped."
        ),
    )
    ap.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help=(
            "Do not load existing shards; start numbering fresh (may overwrite "
            "and re-capture). Resume is enabled by default."
        ),
    )
    ap.set_defaults(resume=True)
    ap.add_argument(
        "--checkpoint-interval",
        type=int,
        default=50,
        help=(
            "Scroll steps between atomic checkpoints of the in-progress shard "
            "(default: 50)."
        ),
    )
    ap.add_argument(
        "--merge-after",
        action="store_true",
        help=(
            "After a clean run, merge all shards into a single sorted file at "
            "--output."
        ),
    )
    ap.add_argument(
        "--merge-only",
        action="store_true",
        help=(
            "Skip scraping; merge existing shards into --output and exit."
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

    if not args.group:
        ap.error("--group is required unless --merge-only is used.")

    config = ScraperConfig(
        group=args.group,
        output=args.output,
        profile_dir=args.profile_dir,
        scroll_pause=args.scroll_pause,
        max_top_scrolls=args.max_top_scrolls,
        scroll_to_chat_start=args.scroll_to_chat_start,
        shard_size=args.shard_size,
        from_date=args.from_date,
        to_date=args.to_date,
        resume=args.resume,
        checkpoint_interval=args.checkpoint_interval,
    )

    scraper = WhatsAppScraper(config)
    messages = scraper.scrape()

    log(
        f"✓  Saved {len(messages)} messages this run to shards "
        f"alongside {Path(config.output).name}"
    )

    if args.merge_after:
        merge_shards(config.output)
