"""Merge sharded scrape output into a single, globally-sorted JSON file.

The live scraper writes ``{stem}.part{NNN}{suffix}`` shards for crash safety.
This helper concatenates every shard, de-duplicates by message id, sorts the
whole set chronologically, re-applies the cross-message sender-fill pass, and
writes one consolidated file at the base output path.

Run standalone::

    python -m wa_scraper.merge --output wa_chat.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

from wa_scraper.contact import Contact
from wa_scraper.models import LinkPreview, Message, Quote, Reaction
from wa_scraper.postprocess import fill_grouped_senders, sort_messages
from wa_scraper.utils import log


def _contact_from(data: Optional[dict]) -> Optional[Contact]:
    if not isinstance(data, dict):
        return None
    return Contact(
        user_name=data.get("user_name"),
        phone=data.get("phone"),
    )


def _message_from(data: dict) -> Optional[Message]:
    mid = data.get("id")
    if not mid:
        return None

    quote_data = data.get("quote")
    quote = None
    if isinstance(quote_data, dict):
        quote = Quote(
            sender=_contact_from(quote_data.get("sender")) or Contact(),
            text=quote_data.get("text", ""),
        )

    reactions: List[Reaction] = []
    for r in data.get("reactions") or []:
        if not isinstance(r, dict):
            continue
        senders = [
            c for c in (
                _contact_from(s) for s in (r.get("senders") or [])
            ) if c is not None
        ]
        reactions.append(
            Reaction(
                emoji=r.get("emoji", ""),
                count=r.get("count", len(senders)),
                senders=senders,
            )
        )

    link_data = data.get("link_preview")
    link_preview = None
    if isinstance(link_data, dict):
        link_preview = LinkPreview(
            url=link_data.get("url", ""),
            title=link_data.get("title", ""),
            description=link_data.get("description", ""),
        )

    return Message(
        id=mid,
        date=data.get("date", ""),
        time=data.get("time", ""),
        sender=_contact_from(data.get("sender")) or Contact(),
        text=data.get("text", ""),
        is_outgoing=bool(data.get("is_outgoing", False)),
        quote=quote,
        reactions=reactions,
        media_type=data.get("media_type"),
        is_forwarded=bool(data.get("is_forwarded", False)),
        is_deleted=bool(data.get("is_deleted", False)),
        link_preview=link_preview,
    )


def find_shards(base_output: str | Path) -> List[Path]:
    """Return the shard files for ``base_output`` ordered by part index."""
    base = Path(base_output)
    stem = base.stem
    suffix = base.suffix or ".json"
    parent = base.parent if str(base.parent) else Path(".")
    index_re = re.compile(rf"\.part(\d+){re.escape(suffix)}$")

    def sort_key(p: Path) -> int:
        m = index_re.search(p.name)
        return int(m.group(1)) if m else 0

    return sorted(parent.glob(f"{stem}.part*{suffix}"), key=sort_key)


def merge_shards(base_output: str | Path) -> Optional[Path]:
    """Combine all shards for ``base_output`` into one sorted JSON file."""
    base = Path(base_output)
    shards = find_shards(base)
    if not shards:
        log(f"  ⚠  No shards found for {base.name}; nothing to merge.")
        return None

    by_id: Dict[str, Message] = {}
    for shard in shards:
        try:
            data = json.loads(shard.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log(f"  ⚠  Skipping unreadable shard {shard.name}: {exc}")
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            msg = _message_from(item)
            if msg is not None:
                by_id[msg.id] = msg

    ordered = sort_messages(by_id.values())
    fill_grouped_senders(ordered)

    payload = [asdict(m) for m in ordered]
    base.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(
        f"✓  Merged {len(shards)} shard(s) → {base} "
        f"({len(ordered)} messages)."
    )
    return base


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Merge sharded WhatsApp scrape output into one JSON file.",
    )
    ap.add_argument(
        "--output",
        default="wa_chat.json",
        help=(
            "Base output path whose shards (<stem>.partNNN<suffix>) are merged "
            "into this file (default: wa_chat.json)."
        ),
    )
    args = ap.parse_args()
    merge_shards(args.output)


if __name__ == "__main__":
    main()
