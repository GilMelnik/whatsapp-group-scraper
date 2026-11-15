"""Unit tests for the crypt15 exporter using synthetic SQLite fixtures."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from wa_android.export import ExportConfig, export_group
from wa_android.parser import GroupDatabaseParser

# The exact shape wiki-from-whatsapp's step 1 consumes (Message.from_android_dict).
EXPECTED_KEYS = {
    "id",
    "date",
    "time",
    "sender",
    "text",
    "is_outgoing",
    "quote",
    "reactions",
    "media_type",
    "is_forwarded",
    "is_deleted",
    "link_preview",
    "mentions",
}


def _build_modern_fixture(base: Path) -> Path:
    msgstore = base / "msgstore.db"

    conn = sqlite3.connect(msgstore)
    conn.executescript(
        """
        CREATE TABLE jid (
            _id INTEGER PRIMARY KEY,
            user TEXT
        );
        CREATE TABLE chat (
            _id INTEGER PRIMARY KEY,
            jid_row_id INTEGER,
            subject TEXT
        );
        CREATE TABLE message (
            _id INTEGER PRIMARY KEY,
            chat_row_id INTEGER,
            from_me INTEGER,
            key_id TEXT,
            status INTEGER,
            message_type INTEGER,
            text_data TEXT,
            timestamp INTEGER,
            sender_jid_row_id INTEGER,
            forwarded INTEGER DEFAULT 0
        );
        CREATE TABLE message_quoted (
            _id INTEGER PRIMARY KEY,
            message_row_id INTEGER,
            from_me INTEGER,
            sender_jid_row_id INTEGER,
            text_data TEXT
        );
        CREATE TABLE message_add_on (
            _id INTEGER PRIMARY KEY,
            parent_message_row_id INTEGER,
            message_add_on_type INTEGER,
            from_me INTEGER DEFAULT 0,
            sender_jid_row_id INTEGER DEFAULT -1
        );
        CREATE TABLE message_add_on_reaction (
            _id INTEGER PRIMARY KEY,
            message_add_on_row_id INTEGER,
            reaction TEXT,
            sender_timestamp INTEGER
        );

        INSERT INTO jid (_id, user) VALUES
            (1, '120363000000000000@g.us'),
            (2, '972501234567@s.whatsapp.net'),
            (3, '972509876543@s.whatsapp.net');
        INSERT INTO chat (_id, jid_row_id, subject) VALUES (1, 1, 'Test Group');
        INSERT INTO message (_id, chat_row_id, from_me, key_id, status, message_type,
                            text_data, timestamp, sender_jid_row_id, forwarded) VALUES
            (1, 1, 0, 'MSG001', 0, 0, 'Hello group', 1717200000000, 2, 0),
            (2, 1, 1, 'MSG002', 0, 0, 'My reply', 1717203600000, NULL, 0),
            (3, 1, 0, 'MSG003', 0, 0, 'Quoted answer', 1717207200000, 3, 1),
            (4, 1, 1, 'SYS001', 6, 7, 'Test Group', 1717210000000, NULL, 0),
            (5, 1, 1, 'SYS002', 6, 7, NULL, 1717211000000, NULL, 0);
        INSERT INTO message_quoted (message_row_id, from_me, sender_jid_row_id, text_data)
            VALUES (3, 0, 2, 'Hello group');
        INSERT INTO message_add_on (_id, parent_message_row_id, message_add_on_type, from_me, sender_jid_row_id)
            VALUES (10, 1, 56, 0, 3);
        INSERT INTO message_add_on_reaction (message_add_on_row_id, reaction, sender_timestamp)
            VALUES (10, '👍', 0);
        """
    )
    conn.commit()
    conn.close()

    return msgstore


class ModernParserTests(unittest.TestCase):
    def test_modern_group_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            msgstore = _build_modern_fixture(base)
            with GroupDatabaseParser(str(msgstore)) as parser:
                groups = parser.list_groups()
                self.assertEqual(len(groups), 1)
                self.assertEqual(groups[0][0], "Test Group")

                messages = parser.iter_messages("Test Group")
                self.assertEqual(len(messages), 3)

                first = messages[0]
                self.assertEqual(first.id, "MSG001")
                self.assertEqual(first.text, "Hello group")
                self.assertEqual(first.sender.user_name, "972501234567@s.whatsapp.net")
                self.assertRegex(first.time, r"^\d\d:\d\d:\d\d$")
                self.assertFalse(first.is_outgoing)
                self.assertEqual(len(first.reactions), 1)
                self.assertEqual(first.reactions[0].emoji, "👍")
                self.assertEqual(first.reactions[0].count, 1)

                outgoing = messages[1]
                self.assertTrue(outgoing.is_outgoing)
                self.assertEqual(outgoing.sender.user_name, "You")

                quoted = messages[2]
                self.assertTrue(quoted.is_forwarded)
                self.assertIsNotNone(quoted.quote)
                assert quoted.quote is not None
                self.assertEqual(quoted.quote.text, "Hello group")


class ExportSchemaContractTests(unittest.TestCase):
    """Guard that the exported JSON stays consumable by wiki step 1."""

    def test_export_matches_step1_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            msgstore = _build_modern_fixture(base)
            output = base / "chat_android.json"
            export_group(
                ExportConfig(group="Test Group", db=str(msgstore), output=str(output))
            )

            records = json.loads(output.read_text(encoding="utf-8"))
            self.assertIsInstance(records, list)
            self.assertEqual(len(records), 3)

            for rec in records:
                self.assertEqual(set(rec.keys()), EXPECTED_KEYS)
                self.assertIsInstance(rec["sender"], dict)
                self.assertIn("user_name", rec["sender"])
                self.assertIn("phone", rec["sender"])
                # date/time formats must match step 1's parse_android_datetime.
                datetime.strptime(rec["date"], "%d/%m/%Y")
                datetime.strptime(rec["time"], "%H:%M:%S")
                self.assertEqual(rec["mentions"], [])
                for rx in rec["reactions"]:
                    self.assertIn("emoji", rx)
                    self.assertIsInstance(rx["senders"], list)


class DecryptKeyTests(unittest.TestCase):
    def test_normalize_hex_key(self) -> None:
        from wa_android.decrypt import normalize_key

        key = normalize_key("a" * 64)
        self.assertEqual(key, "a" * 64)


if __name__ == "__main__":
    unittest.main()
