"""Order, member ids, tags and the members file, on synthetic msgstore databases."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from wa_android.export import ExportConfig, export_group
from wa_android.members import MemberTable
from wa_android.parser import GroupDatabaseParser

GROUP = "120363000000000001@g.us"
P1 = "15550000001@s.whatsapp.net"  # a member, phone era
L1 = "900000000001@lid"  # the same member, lid era
L2 = "900000000002@lid"  # a member the database knows no phone for
P2 = "15550000002@s.whatsapp.net"
P4 = "15550000004@s.whatsapp.net"  # P1's new number

SCHEMA = """
CREATE TABLE jid (_id INTEGER PRIMARY KEY, raw_string TEXT);
CREATE TABLE chat (_id INTEGER PRIMARY KEY, jid_row_id INTEGER, subject TEXT);
CREATE TABLE message (
    _id INTEGER PRIMARY KEY, chat_row_id INTEGER, from_me INTEGER, key_id TEXT,
    status INTEGER, message_type INTEGER, text_data TEXT, timestamp INTEGER,
    sender_jid_row_id INTEGER, forwarded INTEGER DEFAULT 0);
CREATE TABLE message_quoted (
    message_row_id INTEGER PRIMARY KEY, from_me INTEGER, sender_jid_row_id INTEGER,
    text_data TEXT);
CREATE TABLE message_add_on (
    _id INTEGER PRIMARY KEY, parent_message_row_id INTEGER, message_add_on_type INTEGER,
    from_me INTEGER DEFAULT 0, sender_jid_row_id INTEGER DEFAULT -1);
CREATE TABLE message_add_on_reaction (
    _id INTEGER PRIMARY KEY, message_add_on_row_id INTEGER, reaction TEXT);
CREATE TABLE jid_map (lid_row_id INTEGER PRIMARY KEY, jid_row_id INTEGER, sort_id INTEGER);
CREATE TABLE message_mentions (
    _id INTEGER PRIMARY KEY AUTOINCREMENT, message_row_id INTEGER, jid_row_id INTEGER,
    display_name TEXT, mention_type INTEGER);
CREATE TABLE message_system_number_change (
    message_row_id INTEGER PRIMARY KEY, old_jid_row_id INTEGER, new_jid_row_id INTEGER);
"""

# 2024-03-01 10:15:00 local time, in milliseconds.
BASE_MS = int(datetime(2024, 3, 1, 10, 15, 0).timestamp() * 1000)

Row = Tuple[int, str, int, Optional[str], str]  # row id, key id, ms offset, sender, text


class Fixture:
    """Builds one synthetic msgstore.db for the group."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.jids: Dict[str, int] = {}
        self.conn.execute("INSERT INTO chat VALUES (1, ?, 'Test Group')", (self.jid(GROUP),))

    def jid(self, raw: str) -> int:
        if raw not in self.jids:
            self.jids[raw] = len(self.jids) + 1
            self.conn.execute("INSERT INTO jid VALUES (?, ?)", (self.jids[raw], raw))
        return self.jids[raw]

    def messages(self, rows: Sequence[Row]) -> None:
        for row_id, key, offset, sender, text in rows:
            self.conn.execute(
                "INSERT INTO message VALUES (?, 1, ?, ?, 0, 0, ?, ?, ?, 0)",
                (row_id, int(sender is None), key, text, BASE_MS + offset,
                 self.jid(sender) if sender else 0),
            )

    def link(self, lid: str, phone: str) -> None:
        self.conn.execute("INSERT INTO jid_map VALUES (?, ?, 0)", (self.jid(lid), self.jid(phone)))

    def number_change(self, row_id: int, old: str, new: str) -> None:
        self.conn.execute(
            "INSERT INTO message VALUES (?, 1, 0, ?, 6, 7, NULL, ?, ?, 0)",
            (row_id, f"SYS{row_id}", BASE_MS, self.jid(new)),
        )
        self.conn.execute(
            "INSERT INTO message_system_number_change VALUES (?, ?, ?)",
            (row_id, self.jid(old), self.jid(new)),
        )

    def quote(self, row_id: int, sender: str, text: str) -> None:
        self.conn.execute(
            "INSERT INTO message_quoted VALUES (?, 0, ?, ?)", (row_id, self.jid(sender), text)
        )

    def reaction(self, add_on_id: int, row_id: int, sender: str, emoji: str) -> None:
        self.conn.execute(
            "INSERT INTO message_add_on VALUES (?, ?, 56, 0, ?)",
            (add_on_id, row_id, self.jid(sender)),
        )
        self.conn.execute(
            "INSERT INTO message_add_on_reaction (message_add_on_row_id, reaction) VALUES (?, ?)",
            (add_on_id, emoji),
        )

    def mention(self, row_id: int, tagged: str) -> None:
        self.conn.execute(
            "INSERT INTO message_mentions (message_row_id, jid_row_id) VALUES (?, ?)",
            (row_id, self.jid(tagged)),
        )

    def close(self) -> Path:
        self.conn.commit()
        self.conn.close()
        return self.path


def _export(db: Path, members: Optional[Path] = None) -> List[dict]:
    output = db.parent / f"{db.stem}.json"
    export_group(
        ExportConfig(
            group="Test Group",
            db=str(db),
            output=str(output),
            members=str(members) if members else None,
        )
    )
    return json.loads(output.read_text(encoding="utf-8"))


class OrderTests(unittest.TestCase):
    def test_seconds_and_database_order_inside_a_minute_and_a_second(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp) / "msgstore.db")
            fixture.messages([
                # Same minute: the key ids sort the other way round from the times.
                (1, "A-LATER", 50_000, P1, "second of the minute"),
                (2, "B-EARLIER", 10_000, P2, "first of the minute"),
                # Same millisecond: only the row id orders them.
                (3, "Z-FIRST", 70_000, P1, "first in the second"),
                (4, "Y-SECOND", 70_000, P2, "second in the second"),
            ])
            records = _export(fixture.close())
        self.assertEqual(
            [r["id"] for r in records], ["B-EARLIER", "A-LATER", "Z-FIRST", "Y-SECOND"]
        )
        self.assertEqual(
            [r["time"] for r in records], ["10:15:10", "10:15:50", "10:16:10", "10:16:10"]
        )

    def test_database_is_opened_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp) / "msgstore.db")
            fixture.messages([(1, "M1", 0, P1, "hello")])
            db = fixture.close()
            before = hashlib.sha1(db.read_bytes()).hexdigest()
            with GroupDatabaseParser(str(db)) as parser:
                parser.iter_messages("Test Group")
                with self.assertRaises(sqlite3.OperationalError):
                    parser._conn.execute("DELETE FROM message")
            self.assertEqual(hashlib.sha1(db.read_bytes()).hexdigest(), before)


class MemberIdTests(unittest.TestCase):
    def _two_era_fixture(self, tmp: str) -> Path:
        fixture = Fixture(Path(tmp) / "msgstore.db")
        fixture.link(L1, P1)
        fixture.messages([
            (1, "OLD", 0, P1, "written with the phone id"),
            (2, "NEW", 60_000, L1, "written with the lid id"),
            (3, "REPLY", 120_000, L2, f"tagging @{P1.split('@')[0]} and @{L1.split('@')[0]}"
                                      f" and @{L2.split('@')[0]} and @12345 (not a tag)"),
            (4, "OWN", 180_000, None, "my own message"),
        ])
        fixture.quote(3, L1, "written with the lid id")
        fixture.reaction(10, 1, L1, "👍")
        fixture.reaction(11, 1, P2, "👍")
        fixture.mention(3, P1)
        fixture.mention(3, L1)
        fixture.mention(3, L2)
        return fixture.close()

    def test_one_member_under_two_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            records = {r["id"]: r for r in _export(self._two_era_fixture(tmp))}
        self.assertEqual(records["OLD"]["sender"]["user_name"], P1)
        self.assertEqual(records["NEW"]["sender"]["user_name"], P1)
        self.assertEqual(records["REPLY"]["sender"]["user_name"], L2)
        self.assertEqual(records["OWN"]["sender"]["user_name"], "You")
        self.assertIsNone(records["NEW"]["sender"]["phone"])

    def test_quote_and_reaction_by_the_lid_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            records = {r["id"]: r for r in _export(self._two_era_fixture(tmp))}
        self.assertEqual(records["REPLY"]["quote"]["sender"]["user_name"], P1)
        reaction = records["OLD"]["reactions"][0]
        self.assertEqual([s["user_name"] for s in reaction["senders"]], [P1, P2])
        self.assertEqual(reaction["count"], 2)

    def test_tags_by_phone_and_by_lid_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            records = {r["id"]: r for r in _export(self._two_era_fixture(tmp))}
        self.assertEqual(
            records["REPLY"]["mentions"],
            [
                {"tag": P1.split("@")[0], "member": P1},
                {"tag": L1.split("@")[0], "member": P1},
                {"tag": L2.split("@")[0], "member": L2},
            ],
        )
        self.assertIn("@12345", records["REPLY"]["text"])  # the text is not changed
        self.assertEqual(records["OLD"]["mentions"], [])

    def test_number_change_without_members_file_keeps_the_first_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(Path(tmp) / "msgstore.db")
            fixture.messages([(1, "BEFORE", 0, P1, "old number"), (3, "AFTER", 60_000, P4, "new")])
            fixture.number_change(2, P1, P4)
            records = _export(fixture.close())
        self.assertEqual([r["sender"]["user_name"] for r in records], [P1, P1])


class MembersFileTests(unittest.TestCase):
    def test_members_file_keeps_ids_across_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            members = Path(tmp) / "members.json"
            first_dir, second_dir = Path(tmp) / "first", Path(tmp) / "second"
            first_dir.mkdir()
            second_dir.mkdir()

            # First backup: the lid has no phone yet, so it is its own member.
            first = Fixture(first_dir / "msgstore.db")
            first.messages([(1, "M1", 0, P1, "phone era"), (2, "M2", 60_000, L2, "lid only")])
            first_records = _export(first.close(), members)
            self.assertEqual([r["sender"]["user_name"] for r in first_records], [P1, L2])
            saved = json.loads(members.read_text(encoding="utf-8"))
            self.assertEqual(
                saved, {"members": [{"id": P1, "aliases": [P1]}, {"id": L2, "aliases": [L2]}]}
            )

            # Second backup: the lid gained a phone, and P1 changed number to P4.
            second = Fixture(second_dir / "msgstore.db")
            second.link(L2, P2)
            second.link(L1, P4)
            second.messages([
                (1, "M1", 0, P1, "phone era"),
                (2, "M2", 60_000, L2, "lid only"),
                (4, "M3", 120_000, P2, "the lid's phone"),
                (5, "M4", 180_000, L1, "P1 after the change"),
            ])
            second.number_change(3, P1, P4)
            second_records = _export(second.close(), members)
            self.assertEqual(
                [r["sender"]["user_name"] for r in second_records], [P1, L2, L2, P1]
            )
            saved = json.loads(members.read_text(encoding="utf-8"))
            self.assertEqual(
                saved,
                {"members": [{"id": P1, "aliases": sorted([P1, P4, L1])},
                             {"id": L2, "aliases": sorted([L2, P2])}]},
            )

    def test_link_between_two_known_members_is_counted_not_merged(self) -> None:
        table = MemberTable({P1: P1, L2: L2})
        table.resolve([P1, L2, P2], [(L2, P1)], [P1, L2, P2])
        self.assertEqual(table.conflicts, 1)
        self.assertEqual(table.member(P1), P1)
        self.assertEqual(table.member(L2), L2)
        self.assertEqual(table.member(P2), P2)  # not linked: a new member of its own


if __name__ == "__main__":
    unittest.main()
