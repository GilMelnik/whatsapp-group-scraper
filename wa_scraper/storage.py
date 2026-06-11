"""Crash-safe, size-capped sharded JSON writer for scraped messages.

Messages are streamed into ``{stem}.part{NNN}{suffix}`` files as they are
captured. Each shard is written atomically (temp file + ``os.replace``) so an
interrupted write can never corrupt a part. A partially-filled shard is
periodically checkpointed to disk, and flushed once more on close, so a crash
or interrupt loses at most the messages captured since the last checkpoint.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import List, Optional, Set

from wa_scraper.contact import Contact
from wa_scraper.models import Message
from wa_scraper.postprocess import (
    fill_grouped_senders,
    parse_chat_date,
    sort_messages,
)
from wa_scraper.utils import log


class ShardWriter:
    """Streams captured messages to size-capped, internally-sorted JSON shards."""

    def __init__(
        self,
        base_output: str | os.PathLike,
        shard_size: int = 5000,
        resume: bool = True,
    ) -> None:
        self.base = Path(base_output)
        self.shard_size = max(1, int(shard_size))
        self.buffer: List[Message] = []
        self.seen_written: Set[str] = set()
        self.prior_count = 0
        self.finalized_count = 0
        self.current_index = 1
        self.oldest_date: Optional[date] = None
        self._carry_sender: Optional[Contact] = None
        self._carry_date = ""

        if resume:
            self._scan_existing()

    # ── paths ────────────────────────────────────────────────────────────────
    def _shard_path(self, index: int) -> Path:
        stem = self.base.stem
        suffix = self.base.suffix or ".json"
        return self.base.with_name(f"{stem}.part{index:03d}{suffix}")

    # ── resume scan ──────────────────────────────────────────────────────────
    def _scan_existing(self) -> None:
        stem = self.base.stem
        suffix = self.base.suffix or ".json"
        parent = self.base.parent if str(self.base.parent) else Path(".")
        pattern = f"{stem}.part*{suffix}"
        index_re = re.compile(rf"\.part(\d+){re.escape(suffix)}$")

        parts = sorted(parent.glob(pattern))
        if not parts:
            return

        max_index = 0
        loaded = 0
        for p in parts:
            m = index_re.search(p.name)
            if m:
                max_index = max(max_index, int(m.group(1)))
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log(f"  ⚠  Could not read shard {p.name}; skipping for resume.")
                continue
            if not isinstance(data, list):
                continue
            for item in data:
                mid = item.get("id") if isinstance(item, dict) else None
                if not mid or mid in self.seen_written:
                    continue
                self.seen_written.add(mid)
                loaded += 1
                self._update_oldest(item.get("date") or "")

        self.prior_count = loaded
        self.current_index = max_index + 1
        if loaded:
            oldest = (
                self.oldest_date.strftime("%d/%m/%Y")
                if self.oldest_date
                else "?"
            )
            log(
                f"↩  Resuming: {loaded} message(s) already captured across "
                f"{len(parts)} shard(s); oldest date so far {oldest}. "
                f"Next shard: part{self.current_index:03d}."
            )

    # ── public API ───────────────────────────────────────────────────────────
    def has(self, mid: str) -> bool:
        """Whether a message id was already persisted (this run or a prior one)."""
        return bool(mid) and mid in self.seen_written

    def add(self, msg: Message) -> bool:
        """Buffer a brand-new message; finalize a shard when the cap is reached."""
        if not msg.id or msg.id in self.seen_written:
            return False
        self.seen_written.add(msg.id)
        self.buffer.append(msg)
        self._update_oldest(msg.date)
        if len(self.buffer) >= self.shard_size:
            self._finalize()
        return True

    def checkpoint(self) -> None:
        """Atomically persist the current partial shard without finalizing it."""
        if not self.buffer:
            return
        ordered = self._prepare(advance_carry=False)
        self._write_atomic(self._shard_path(self.current_index), ordered)

    def close(self) -> None:
        """Flush any remaining buffered messages as the final shard."""
        if self.buffer:
            self._finalize()

    @property
    def total_captured(self) -> int:
        """Unique messages captured overall (prior shards + this run)."""
        return len(self.seen_written)

    # ── internals ──────────────────────────────────────────────────────────--
    def _finalize(self) -> None:
        if not self.buffer:
            return
        ordered = self._prepare(advance_carry=True)
        path = self._shard_path(self.current_index)
        self._write_atomic(path, ordered)
        self.finalized_count += len(ordered)
        log(f"  💾  wrote shard {path.name} ({len(ordered)} messages)")
        self.buffer = []
        self.current_index += 1

    def _prepare(self, *, advance_carry: bool) -> List[Message]:
        ordered = sort_messages(self.buffer)
        last_sender, last_date = fill_grouped_senders(
            ordered,
            last_sender=self._carry_sender,
            last_date=self._carry_date,
        )
        if advance_carry:
            self._carry_sender = last_sender
            self._carry_date = last_date
        return ordered

    def _write_atomic(self, path: Path, msgs: List[Message]) -> None:
        payload = [asdict(m) for m in msgs]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def _update_oldest(self, date_str: str) -> None:
        d = parse_chat_date(date_str)
        if d is None:
            return
        if self.oldest_date is None or d < self.oldest_date:
            self.oldest_date = d
