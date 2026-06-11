"""High-level export orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional, Union

from wa_scraper.models import Message
from wa_scraper.postprocess import parse_chat_date
from wa_scraper.storage import ShardWriter

from wa_android.decrypt import ensure_plaintext_db, ensure_plaintext_msgstore
from wa_android.discover import resolve_databases
from wa_android.legacy_backup import run_legacy_backup
from wa_android.logutil import log
from wa_android.parser import GroupDatabaseParser


@dataclass
class ExportConfig:
    group: str
    output: str = "wa_chat.json"
    device: Optional[str] = None
    cache_dir: Optional[str] = None
    db: Optional[str] = None
    wa_db: Optional[str] = None
    backup_key: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    shard_size: int = 5000
    resume: bool = True
    pull_only: bool = False
    legacy_backup: bool = False


def _in_date_window(
    msg: Message,
    from_date: Optional[date],
    to_date: Optional[date],
) -> bool:
    msg_date = parse_chat_date(msg.date)
    if msg_date is None:
        return True
    if from_date and msg_date < from_date:
        return False
    if to_date and msg_date > to_date:
        return False
    return True


def export_group(config: ExportConfig) -> List[Message]:
    cache_override = Path(config.cache_dir).expanduser() if config.cache_dir else None

    if config.legacy_backup:
        discovered, cache_dir = run_legacy_backup(
            serial=config.device,
            cache_dir=cache_override,
        )
    else:
        discovered, cache_dir = resolve_databases(
            serial=config.device,
            cache_dir=cache_override,
            db_path=config.db,
            wa_db_path=config.wa_db,
            pull_only=config.pull_only,
        )

    if config.pull_only and not config.legacy_backup:
        return []

    msgstore = ensure_plaintext_msgstore(discovered, cache_dir, config.backup_key)
    wa_db = config.wa_db
    if wa_db:
        wa_raw = Path(wa_db).expanduser().resolve()
    elif discovered.wa_db:
        wa_raw = discovered.wa_db
    else:
        wa_raw = None

    wa_path = None
    if wa_raw and wa_raw.is_file():
        key = config.backup_key or (
            str(discovered.key_file) if discovered.key_file else None
        )
        wa_path = str(
            ensure_plaintext_db(wa_raw, cache_dir, "wa.db", key, discovered.key_file)
        )
    elif not wa_raw:
        log("⚠  wa.db not found — contact names may be missing.")

    from_d = parse_chat_date(config.from_date) if config.from_date else None
    to_d = parse_chat_date(config.to_date) if config.to_date else None

    writer = ShardWriter(
        config.output,
        shard_size=config.shard_size,
        resume=config.resume,
    )

    exported: List[Message] = []
    with GroupDatabaseParser(str(msgstore), wa_path) as parser:
        log(f"↑  Exporting group {config.group!r} from {msgstore.name} …")
        for msg in parser.iter_messages(config.group):
            if not _in_date_window(msg, from_d, to_d):
                continue
            if writer.add(msg):
                exported.append(msg)

    writer.close()
    total = writer.total_captured
    log(
        f"✓  Exported {len(exported)} message(s) this run "
        f"({total} total across shards) → {config.output}"
    )
    return exported
