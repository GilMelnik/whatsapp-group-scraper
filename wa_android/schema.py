"""WhatsApp msgstore.db schema detection and metadata."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Set


@dataclass(frozen=True)
class SchemaInfo:
    variant: str  # "modern" | "legacy"
    tables: Set[str]
    message_table: str
    has_message_quoted: bool
    has_message_add_on: bool
    has_chat: bool
    has_jid: bool


def _table_names(conn: sqlite3.Connection) -> Set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {row[0] for row in rows}


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return []
    return [row[1] for row in rows]


def detect_schema(conn: sqlite3.Connection) -> SchemaInfo:
    tables = _table_names(conn)
    if "message" in tables:
        return SchemaInfo(
            variant="modern",
            tables=tables,
            message_table="message",
            has_message_quoted="message_quoted" in tables,
            has_message_add_on="message_add_on" in tables,
            has_chat="chat" in tables,
            has_jid="jid" in tables,
        )
    if "messages" in tables:
        return SchemaInfo(
            variant="legacy",
            tables=tables,
            message_table="messages",
            has_message_quoted=False,
            has_message_add_on=False,
            has_chat="chat_list" in tables,
            has_jid=False,
        )
    raise ValueError("Unrecognized WhatsApp database schema (no message table).")


def attach_wa_db(conn: sqlite3.Connection, wa_db_path: Optional[str]) -> bool:
    if not wa_db_path:
        return False
    try:
        conn.execute("DETACH DATABASE wa")
    except sqlite3.Error:
        pass
    try:
        conn.execute("ATTACH DATABASE ? AS wa", (wa_db_path,))
        return True
    except sqlite3.Error:
        return False


# message_type → media_type (modern + legacy media_wa_type)
MEDIA_TYPE_MAP: Dict[int, Optional[str]] = {
    0: None,
    1: "image",
    2: "audio",
    3: "video",
    4: "document",
    5: "location",
    9: "document",
    13: "gif",
    20: "sticker",
    15: None,  # deleted
    7: None,   # system
}

# WhatsApp stores group events (member joined, group created, etc.) as
# message_type 7 with status 6 (control message). They often have from_me=1
# but are not user-authored chat messages.
SYSTEM_MESSAGE_TYPES = frozenset({7, 112, 90, 118, 43, 14})


def is_system_message(message_type: Optional[int], status: Optional[int] = None) -> bool:
    """Return True for WhatsApp system/control rows that are not chat messages."""
    msg_type = int(message_type or 0)
    if msg_type in SYSTEM_MESSAGE_TYPES:
        return True
    # status 6 = control/system message in modern msgstore
    if int(status or 0) == 6:
        return True
    return False


def is_deleted_message(message_type: Optional[int]) -> bool:
    """Return True only for tombstoned/deleted messages."""
    return int(message_type or 0) == 15


def map_media_type(value: Optional[int], text: str = "") -> Optional[str]:
    if value is None:
        return None
    mapped = MEDIA_TYPE_MAP.get(int(value))
    if mapped:
        return mapped
    if int(value) == 0:
        return None
    if int(value) in {15, 7}:
        return None
    return "document"


def pick_column(columns: List[str], *candidates: str) -> Optional[str]:
    lower = {c.lower(): c for c in columns}
    for name in candidates:
        if name.lower() in lower:
            return lower[name.lower()]
    return None
