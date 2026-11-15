"""High-level export orchestration: crypt15 file -> one chat_android.json."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

from wa_android.models import Message
from wa_android.postprocess import parse_chat_date

from wa_android.decrypt import ensure_plaintext_msgstore
from wa_android.discover import resolve_databases
from wa_android.logutil import log
from wa_android.members import MemberTable
from wa_android.parser import GroupDatabaseParser


@dataclass
class ExportConfig:
    group: str
    db: str
    output: str = "chat_android.json"
    backup_key: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    members: Optional[str] = None


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
    """Write the group's messages (database order, member ids) to ``config.output``; with
    ``config.members``, read that members file first and write it back extended."""
    discovered, work_dir = resolve_databases(db_path=config.db, wa_db_path=None)
    msgstore = ensure_plaintext_msgstore(discovered, work_dir, config.backup_key)

    from_d = parse_chat_date(config.from_date) if config.from_date else None
    to_d = parse_chat_date(config.to_date) if config.to_date else None

    members_path = Path(config.members).expanduser() if config.members else None
    members = MemberTable.load(members_path)
    with GroupDatabaseParser(str(msgstore), members) as parser:
        log(f"Exporting group {config.group!r} from {msgstore.name} …")
        messages = [
            m
            for m in parser.iter_messages(config.group)
            if _in_date_window(m, from_d, to_d)
        ]

    output = Path(config.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([asdict(m) for m in messages], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"Exported {len(messages)} message(s) -> {output}")
    if members_path is not None:
        members.save(members_path)
        log(f"Members file: {len(members.members())} member(s) -> {members_path}")
    return messages
