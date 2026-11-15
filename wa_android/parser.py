"""Parse WhatsApp msgstore.db rows into Message models.

The database is opened read-only and only the chosen group's rows are read. Messages come
in the database's own order (``timestamp`` in milliseconds, then the row id for messages of
the same millisecond); nothing re-sorts them. Every sender, quoted sender, reactor and
tagged member is given as its member id (``wa_android.members``); the account's own
messages, quotes and reactions are "You". The database does not say which phone or lid id
is the account's own, so a tag of the account owner carries a member id like any other.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from wa_android.contact import Contact, clean_contact_name, dedupe_contacts
from wa_android.logutil import log
from wa_android.members import MemberTable
from wa_android.models import Mention, Message, Quote, Reaction
from wa_android.postprocess import fill_grouped_senders

from wa_android.schema import (
    detect_schema,
    is_deleted_message,
    is_system_message,
    map_media_type,
    pick_column,
)

YOU = "You"
REACTION_ADD_ON_TYPE = 56
_TAG_RE = re.compile(r"@(\d+)")


def _timestamp_parts(ts_ms: Optional[int]) -> Tuple[str, str]:
    if not ts_ms:
        return "", ""
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000.0)
    except (OSError, OverflowError, ValueError):
        return "", ""
    return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M:%S")


def _digits(alias: str) -> str:
    return alias.split("@", 1)[0]


def find_mentions(text: str, tagged: List[str], members: MemberTable) -> List[Mention]:
    """One entry per ``@<digits>`` of ``text`` whose digits are a member the database says
    the message tags (``tagged``: raw aliases), in the order of the text."""
    by_digits = {_digits(alias): alias for alias in tagged}
    return [
        Mention(tag=match.group(1), member=members.member(by_digits[match.group(1)]))
        for match in _TAG_RE.finditer(text)
        if match.group(1) in by_digits
    ]


class GroupDatabaseParser:
    """Reads one group chat from a plaintext msgstore.db, read-only."""

    def __init__(self, msgstore_path: str, members: Optional[MemberTable] = None) -> None:
        self.msgstore_path = msgstore_path
        uri = f"{Path(msgstore_path).expanduser().resolve().as_uri()}?mode=ro&immutable=1"
        self._conn = sqlite3.connect(uri, uri=True)
        self._conn.row_factory = sqlite3.Row
        self.schema = detect_schema(self._conn)
        self.members = members or MemberTable()
        self._group_jid: Optional[str] = None
        self.tags_without_mention = 0
        self.mentions_without_tag = 0

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "GroupDatabaseParser":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

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
                groups.append((subject or jid, jid))
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

    def _chat_row_id(self, group_jid: str) -> int:
        row = self._conn.execute(
            f"SELECT c._id FROM chat c JOIN jid gj ON c.jid_row_id = gj._id "
            f"WHERE {self._jid_expr('gj')} = ?",
            (group_jid,),
        ).fetchone()
        if row is None:
            raise ValueError(f"No chat row for group {group_jid!r}.")
        return int(row[0])

    # ------------------------------------------------------------------ raw rows

    def _reaction_rows(self, chat: int) -> List[sqlite3.Row]:
        if not self.schema.has_message_add_on:
            return []
        sql = f"""
            SELECT mao.parent_message_row_id AS msg_id,
                   mar.reaction AS emoji,
                   {self._jid_expr("j")} AS reactor_jid,
                   mao.from_me AS reactor_from_me
            FROM message_add_on mao
            JOIN message_add_on_reaction mar
              ON mar.message_add_on_row_id = mao._id
            JOIN message m ON mao.parent_message_row_id = m._id
            LEFT JOIN jid j ON mao.sender_jid_row_id = j._id
                AND mao.sender_jid_row_id > 0
            WHERE mao.message_add_on_type = {REACTION_ADD_ON_TYPE}
              AND m.chat_row_id = ?
            ORDER BY mao._id
        """
        try:
            return self._conn.execute(sql, (chat,)).fetchall()
        except sqlite3.Error:
            return []

    def _quote_rows(self, chat: int) -> List[sqlite3.Row]:
        if not self.schema.has_message_quoted:
            return []
        sql = f"""
            SELECT mq.message_row_id AS msg_id,
                   mq.text_data AS quote_text,
                   mq.from_me AS quote_from_me,
                   {self._jid_expr("sj")} AS quote_sender_jid
            FROM message_quoted mq
            JOIN message m ON mq.message_row_id = m._id
            LEFT JOIN jid sj ON mq.sender_jid_row_id = sj._id
                AND mq.sender_jid_row_id > 0
            WHERE m.chat_row_id = ?
              AND mq.text_data IS NOT NULL
              AND mq.text_data != ''
        """
        try:
            return self._conn.execute(sql, (chat,)).fetchall()
        except sqlite3.Error:
            return []

    def _mention_rows(self, chat: int) -> Dict[int, List[str]]:
        """Message row id -> raw aliases the message tags."""
        if "message_mentions" not in self.schema.tables:
            return {}
        sql = f"""
            SELECT mm.message_row_id AS msg_id, {self._jid_expr("tj")} AS tagged_jid
            FROM message_mentions mm
            JOIN message m ON mm.message_row_id = m._id
            JOIN jid tj ON mm.jid_row_id = tj._id
            WHERE m.chat_row_id = ?
            ORDER BY mm._id
        """
        out: Dict[int, List[str]] = defaultdict(list)
        for row in self._conn.execute(sql, (chat,)):
            if row["tagged_jid"]:
                out[int(row["msg_id"])].append(str(row["tagged_jid"]))
        return out

    def _message_rows(self, chat: int) -> List[sqlite3.Row]:
        sql = f"""
            SELECT m._id AS row_id,
                   m.key_id AS key_id,
                   m.from_me AS from_me,
                   m.message_type AS message_type,
                   m.text_data AS text_data,
                   m.timestamp AS timestamp,
                   m.status AS status,
                   {self._jid_expr("sj")} AS sender_jid,
                   m.forwarded AS forwarded
            FROM message m
            LEFT JOIN jid sj ON m.sender_jid_row_id = sj._id
            WHERE m.chat_row_id = ?
            ORDER BY m.timestamp ASC, m._id ASC
        """
        if "forwarded" not in _columns_safe(self._conn, "message"):
            sql = sql.replace("m.forwarded AS forwarded", "0 AS forwarded")
        try:
            return self._conn.execute(sql, (chat,)).fetchall()
        except sqlite3.Error as exc:
            raise ValueError(f"Failed to query messages: {exc}") from exc

    def _group_jid_rows_sql(self) -> str:
        """SQL of the jid row ids the group's messages use (``?`` = the chat row id, once
        per part)."""
        parts = ["SELECT sender_jid_row_id AS id FROM message WHERE chat_row_id = ?"]
        joined = "JOIN message m ON {col} = m._id WHERE m.chat_row_id = ?"
        if self.schema.has_message_quoted:
            parts.append("SELECT mq.sender_jid_row_id FROM message_quoted mq "
                         + joined.format(col="mq.message_row_id"))
        if self.schema.has_message_add_on:
            parts.append("SELECT mao.sender_jid_row_id FROM message_add_on mao "
                         + joined.format(col="mao.parent_message_row_id"))
        if "message_mentions" in self.schema.tables:
            parts.append("SELECT mm.jid_row_id FROM message_mentions mm "
                         + joined.format(col="mm.message_row_id"))
        return " UNION ".join(parts)

    def _links(self, chat: int) -> List[Tuple[str, str]]:
        """Pairs of aliases of one member: lid/phone (``jid_map``) of the group's aliases,
        and the number changes the group recorded."""
        links: List[Tuple[str, str]] = []
        tables = self.schema.tables
        if "jid_map" in tables:
            group_rows = self._group_jid_rows_sql()
            parts = group_rows.count("?")
            sql = f"""
                WITH g AS ({group_rows})
                SELECT {self._jid_expr("l")} AS a, {self._jid_expr("p")} AS b
                FROM jid_map jm
                JOIN jid l ON jm.lid_row_id = l._id
                JOIN jid p ON jm.jid_row_id = p._id
                WHERE jm.lid_row_id IN (SELECT id FROM g) OR jm.jid_row_id IN (SELECT id FROM g)
            """
            links += [(str(r["a"]), str(r["b"])) for r in self._conn.execute(sql, (chat,) * parts)]
        if "message_system_number_change" in tables:
            sql = f"""
                SELECT {self._jid_expr("o")} AS a, {self._jid_expr("n")} AS b
                FROM message_system_number_change nc
                JOIN message m ON nc.message_row_id = m._id
                JOIN jid o ON nc.old_jid_row_id = o._id
                JOIN jid n ON nc.new_jid_row_id = n._id
                WHERE m.chat_row_id = ?
            """
            links += [(str(r["a"]), str(r["b"])) for r in self._conn.execute(sql, (chat,))]
        return links

    # ------------------------------------------------------------------ messages

    def _contact(self, alias: str, from_me: bool) -> Contact:
        if from_me:
            return Contact(user_name=YOU, phone=None)
        return Contact(user_name=self.members.member(alias) if alias else "", phone=None)

    def _iter_modern_messages(self, group_jid: str) -> Iterable[Message]:
        chat = self._chat_row_id(group_jid)
        rows = self._message_rows(chat)
        quote_rows = self._quote_rows(chat)
        reaction_rows = self._reaction_rows(chat)
        mention_rows = self._mention_rows(chat)

        seen = [str(r["sender_jid"]) for r in rows if not r["from_me"] and r["sender_jid"]]
        seen += [str(r["quote_sender_jid"]) for r in quote_rows if r["quote_sender_jid"]]
        seen += [str(r["reactor_jid"]) for r in reaction_rows if r["reactor_jid"]]
        seen += [alias for tagged in mention_rows.values() for alias in tagged]
        first_seen = list(dict.fromkeys(seen))
        self.members.resolve(first_seen, self._links(chat), first_seen)

        quotes: Dict[int, Quote] = {
            int(r["msg_id"]): Quote(
                sender=self._contact(str(r["quote_sender_jid"] or ""), bool(r["quote_from_me"])),
                text=str(r["quote_text"] or ""),
            )
            for r in quote_rows
        }
        reactors: Dict[int, Dict[str, List[Contact]]] = defaultdict(lambda: defaultdict(list))
        for r in reaction_rows:
            emoji = str(r["emoji"] or "").strip()
            if emoji:
                reactors[int(r["msg_id"])][emoji].append(
                    self._contact(str(r["reactor_jid"] or ""), bool(r["reactor_from_me"]))
                )

        for row in rows:
            row_id = int(row["row_id"])
            msg_type = row["message_type"]
            if is_system_message(msg_type, row["status"]):
                continue
            text = str(row["text_data"] or "")
            is_deleted = is_deleted_message(msg_type)
            if is_deleted:
                text = ""
            is_out = bool(row["from_me"])
            date_s, time_s = _timestamp_parts(row["timestamp"])
            tagged = mention_rows.get(row_id, [])
            mentions = find_mentions(text, tagged, self.members)
            self.tags_without_mention += len(_TAG_RE.findall(text)) - len(mentions)
            self.mentions_without_tag += len(
                {_digits(alias) for alias in tagged} - {m.tag for m in mentions}
            )
            yield Message(
                id=str(row["key_id"] or row_id),
                date=date_s,
                time=time_s,
                sender=self._contact(str(row["sender_jid"] or ""), is_out),
                text=text,
                is_outgoing=is_out,
                quote=quotes.get(row_id),
                reactions=[
                    Reaction(emoji=emoji, count=len(deduped), senders=deduped)
                    for emoji, senders in reactors.get(row_id, {}).items()
                    for deduped in [dedupe_contacts(senders)]
                ],
                media_type=map_media_type(msg_type, text),
                is_forwarded=bool(row["forwarded"]),
                is_deleted=is_deleted,
                link_preview=None,
                mentions=mentions,
            )

    def iter_messages(self, group_name: str) -> List[Message]:
        """The group's messages in the database's order (time, then row id)."""
        group_jid = self.resolve_group(group_name)
        messages = list(self._iter_modern_messages(group_jid))
        fill_grouped_senders(messages)
        log(
            f"Members: {self.members.new_members} new, {self.members.new_aliases} new aliases, "
            f"{self.members.conflicts} database links between two known members (not merged); "
            f"tags: {sum(len(m.mentions) for m in messages)} found, "
            f"{self.tags_without_mention} '@<digits>' without a tagged member, "
            f"{self.mentions_without_tag} tagged members not written in the text"
        )
        return messages


def _columns_safe(conn: sqlite3.Connection, table: str) -> List[str]:
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    except sqlite3.Error:
        return []
