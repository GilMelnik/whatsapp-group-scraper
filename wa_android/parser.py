"""Parse WhatsApp msgstore.db rows into Message models."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from wa_android.contact import Contact, clean_contact_name, dedupe_contacts
from wa_android.models import Message, Quote, Reaction
from wa_android.postprocess import fill_grouped_senders, sort_messages

from wa_android.schema import (
    attach_wa_db,
    detect_schema,
    is_deleted_message,
    is_system_message,
    map_media_type,
    pick_column,
)


_JID_PHONE_RE = re.compile(r"^(\d+)@")


def _format_phone(jid_user: str) -> Optional[str]:
    user = (jid_user or "").split("@", 1)[0]
    if not user or not user.isdigit():
        return None
    if user.startswith("972") and len(user) >= 11:
        local = user[3:]
        if len(local) == 9:
            return f"+972 {local[:2]}-{local[2:5]}-{local[5:]}"
    if len(user) >= 10:
        return f"+{user}"
    return f"+{user}"


def _timestamp_parts(ts_ms: Optional[int]) -> Tuple[str, str]:
    if not ts_ms:
        return "", ""
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000.0)
    except (OSError, OverflowError, ValueError):
        return "", ""
    return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M")


def _contact_from_jid(
    jid_user: str,
    names: Dict[str, str],
) -> Contact:
    if not jid_user:
        return Contact(user_name="", phone=None)
    name = names.get(jid_user) or names.get(jid_user.split("@", 1)[0])
    if name:
        return Contact(user_name=clean_contact_name(name), phone=None)
    phone = _format_phone(jid_user)
    if phone:
        return Contact(user_name="", phone=phone)
    return Contact(user_name=jid_user, phone=None)


class GroupDatabaseParser:
    """Reads one group chat from msgstore.db (+ optional wa.db)."""

    def __init__(
        self,
        msgstore_path: str,
        wa_db_path: Optional[str] = None,
    ) -> None:
        self.msgstore_path = msgstore_path
        self.wa_db_path = wa_db_path
        self._conn = sqlite3.connect(msgstore_path)
        self._conn.row_factory = sqlite3.Row
        self.schema = detect_schema(self._conn)
        self._wa_attached = attach_wa_db(self._conn, wa_db_path)
        self._names = self._load_contact_names()
        self._group_jid: Optional[str] = None

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "GroupDatabaseParser":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _load_contact_names(self) -> Dict[str, str]:
        names: Dict[str, str] = {}
        queries = []
        if self._wa_attached:
            queries.extend([
                "SELECT jid, display_name AS name FROM wa.wa_contacts WHERE display_name IS NOT NULL",
                "SELECT jid, wa_name AS name FROM wa.wa_contacts WHERE wa_name IS NOT NULL",
            ])
        for sql in queries:
            try:
                for row in self._conn.execute(sql):
                    jid = str(row["jid"] or "")
                    name = clean_contact_name(str(row["name"] or ""))
                    if jid and name:
                        names[jid] = name
                        names[jid.split("@", 1)[0]] = name
            except sqlite3.Error:
                continue
        if self.schema.has_jid:
            jid_col = pick_column(_columns_safe(self._conn, "jid"), "raw_string", "user")
            if jid_col:
                try:
                    for row in self._conn.execute(
                        f"SELECT {jid_col} AS jid FROM jid WHERE {jid_col} IS NOT NULL"
                    ):
                        jid = str(row["jid"] or "")
                        if jid and jid not in names:
                            names[jid] = jid
                            names[jid.split("@", 1)[0]] = jid
                except sqlite3.Error:
                    pass
        return names

    def _jid_expr(self, alias: str = "j") -> str:
        cols = _columns_safe(self._conn, "jid")
        col = pick_column(cols, "raw_string", "user")
        if not col:
            raise ValueError("jid table has no raw_string/user column.")
        return f"{alias}.{col}"

    def list_groups(self) -> List[Tuple[str, str]]:
        """Return (display_name, jid) for group chats."""
        groups: List[Tuple[str, str]] = []
        if self.schema.has_chat and self.schema.has_jid:
            jid_expr = self._jid_expr("j")
            sql = f"""
                SELECT {jid_expr} AS jid,
                       COALESCE(c.subject, '') AS subject
                FROM chat c
                JOIN jid j ON c.jid_row_id = j._id
                WHERE {jid_expr} LIKE '%@g.us'
                ORDER BY c.subject, {jid_expr}
            """
            for row in self._conn.execute(sql):
                jid = str(row["jid"] or "")
                subject = clean_contact_name(str(row["subject"] or ""))
                display = subject or self._names.get(jid, jid)
                groups.append((display, jid))
        return groups

    def resolve_group(self, group_name: str) -> str:
        target = group_name.strip().casefold()
        matches = [
            (display, jid)
            for display, jid in self.list_groups()
            if target in display.casefold() or target in jid.casefold()
        ]
        if not matches:
            available = ", ".join(d for d, _ in self.list_groups()[:8])
            raise ValueError(
                f"Group {group_name!r} not found. Available: {available or '(none)'}"
            )
        if len(matches) > 1:
            exact = [m for m in matches if m[0].casefold() == target]
            if len(exact) == 1:
                matches = exact
            else:
                names = ", ".join(m[0] for m in matches[:5])
                raise ValueError(
                    f"Group name {group_name!r} is ambiguous. Matches: {names}"
                )
        self._group_jid = matches[0][1]
        return self._group_jid

    def _load_reactions_modern(self, group_jid: str) -> Dict[int, List[Reaction]]:
        if not self.schema.has_message_add_on:
            return {}
        reactions_by_msg: Dict[int, Dict[str, List[Contact]]] = defaultdict(
            lambda: defaultdict(list)
        )
        jid_expr = self._jid_expr("j")
        gj_expr = self._jid_expr("gj")
        sql = f"""
            SELECT mao.parent_message_row_id AS msg_id,
                   mar.reaction AS emoji,
                   {jid_expr} AS reactor_jid,
                   mao.from_me AS reactor_from_me
            FROM message_add_on mao
            JOIN message_add_on_reaction mar
              ON mar.message_add_on_row_id = mao._id
            JOIN message m ON mao.parent_message_row_id = m._id
            JOIN chat c ON m.chat_row_id = c._id
            JOIN jid gj ON c.jid_row_id = gj._id
            LEFT JOIN jid j ON mao.sender_jid_row_id = j._id
                AND mao.sender_jid_row_id > 0
            WHERE mao.message_add_on_type = 56
              AND {gj_expr} = ?
        """
        try:
            rows = self._conn.execute(sql, (group_jid,))
        except sqlite3.Error:
            return {}
        for row in rows:
            msg_id = int(row["msg_id"])
            emoji = str(row["emoji"] or "").strip()
            if bool(row["reactor_from_me"]):
                reactor = Contact(user_name="You", phone=None)
            else:
                reactor = _contact_from_jid(
                    str(row["reactor_jid"] or ""), self._names
                )
            if emoji:
                reactions_by_msg[msg_id][emoji].append(reactor)
        out: Dict[int, List[Reaction]] = {}
        for msg_id, emoji_map in reactions_by_msg.items():
            out[msg_id] = []
            for emoji, senders in emoji_map.items():
                deduped = dedupe_contacts(senders)
                out[msg_id].append(
                    Reaction(emoji=emoji, count=len(deduped), senders=deduped)
                )
        return out

    def _load_quotes_modern(self, group_jid: str) -> Dict[int, Quote]:
        if not self.schema.has_message_quoted:
            return {}
        sj_expr = self._jid_expr("sj")
        gj_expr = self._jid_expr("gj")
        sql = f"""
            SELECT mq.message_row_id AS msg_id,
                   mq.text_data AS quote_text,
                   mq.from_me AS quote_from_me,
                   {sj_expr} AS quote_sender_jid
            FROM message_quoted mq
            JOIN message m ON mq.message_row_id = m._id
            JOIN chat c ON m.chat_row_id = c._id
            JOIN jid gj ON c.jid_row_id = gj._id
            LEFT JOIN jid sj ON mq.sender_jid_row_id = sj._id
                AND mq.sender_jid_row_id > 0
            WHERE {gj_expr} = ?
              AND mq.text_data IS NOT NULL
              AND mq.text_data != ''
        """
        try:
            rows = self._conn.execute(sql, (group_jid,))
        except sqlite3.Error:
            return {}
        out: Dict[int, Quote] = {}
        for row in rows:
            msg_id = int(row["msg_id"])
            text = str(row["quote_text"] or "")
            if bool(row["quote_from_me"]):
                sender = Contact(user_name="You", phone=None)
            else:
                sender = _contact_from_jid(
                    str(row["quote_sender_jid"] or ""), self._names
                )
            out[msg_id] = Quote(sender=sender, text=text)
        return out

    def _iter_modern_messages(self, group_jid: str) -> Iterable[Message]:
        reactions = self._load_reactions_modern(group_jid)
        quotes = self._load_quotes_modern(group_jid)
        gj_expr = self._jid_expr("gj")
        sj_expr = self._jid_expr("sj")
        sql = f"""
            SELECT m._id AS row_id,
                   m.key_id AS key_id,
                   m.from_me AS from_me,
                   m.message_type AS message_type,
                   m.text_data AS text_data,
                   m.timestamp AS timestamp,
                   m.status AS status,
                   {sj_expr} AS sender_jid,
                   m.forwarded AS forwarded
            FROM message m
            JOIN chat c ON m.chat_row_id = c._id
            JOIN jid gj ON c.jid_row_id = gj._id
            LEFT JOIN jid sj ON m.sender_jid_row_id = sj._id
            WHERE {gj_expr} = ?
            ORDER BY m.timestamp ASC, m._id ASC
        """
        cols = _columns_safe(self._conn, "message")
        if "forwarded" not in cols:
            sql = sql.replace("m.forwarded AS forwarded", "0 AS forwarded")
        try:
            rows = self._conn.execute(sql, (group_jid,))
        except sqlite3.Error as exc:
            raise ValueError(f"Failed to query messages: {exc}") from exc

        for row in rows:
            row_id = int(row["row_id"])
            msg_type = row["message_type"]
            status = row["status"]
            if is_system_message(msg_type, status):
                continue
            text = str(row["text_data"] or "")
            is_deleted = is_deleted_message(msg_type)
            if is_deleted:
                text = ""
            is_out = bool(row["from_me"])
            if is_out:
                sender = Contact(user_name="You", phone=None)
            else:
                sender = _contact_from_jid(str(row["sender_jid"] or ""), self._names)
            date_s, time_s = _timestamp_parts(row["timestamp"])
            yield Message(
                id=str(row["key_id"] or row_id),
                date=date_s,
                time=time_s,
                sender=sender,
                text=text,
                is_outgoing=is_out,
                quote=quotes.get(row_id),
                reactions=reactions.get(row_id, []),
                media_type=map_media_type(msg_type, text),
                is_forwarded=bool(row["forwarded"]),
                is_deleted=is_deleted,
                link_preview=None,
            )

    def iter_messages(self, group_name: str) -> List[Message]:
        group_jid = self.resolve_group(group_name)
        messages = list(self._iter_modern_messages(group_jid))
        ordered = sort_messages(messages)
        fill_grouped_senders(ordered)
        return ordered


def _columns_safe(conn: sqlite3.Connection, table: str) -> List[str]:
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    except sqlite3.Error:
        return []
