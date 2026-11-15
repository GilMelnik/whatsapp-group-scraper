"""Sender-fill and date parsing helpers.

Messages are never re-sorted here: the parser yields them in the database's order.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from wa_android.contact import Contact
from wa_android.models import Message


def fill_grouped_senders(
    ordered: List[Message],
    *,
    last_sender: Optional[Contact] = None,
    last_date: str = "",
) -> tuple[Optional[Contact], str]:
    """Inherit the sender for header-less messages in a consecutive run.

    WhatsApp renders the author header (name/phone) only on the first message
    of a consecutive run from the same sender; the following messages in the
    run carry no author element and therefore parse with an empty sender.
    Such a message always belongs to the same sender as the message directly
    above it, so we carry the last known sender forward within each day.

    ``last_sender``/``last_date`` allow the carry to span shard boundaries.
    Returns the trailing ``(last_sender, last_date)`` so a caller can chain
    the state across successive shards.
    """
    for msg in ordered:
        if msg.date != last_date:
            last_sender = None
            last_date = msg.date

        if msg.is_outgoing:
            # An outgoing message breaks the incoming run: the next incoming
            # message from any sender gets its own header again.
            last_sender = None
            continue

        sender = msg.sender
        has_details = bool(sender and (sender.user_name or sender.phone))
        if has_details:
            last_sender = sender
        elif last_sender is not None:
            msg.sender = Contact(
                user_name=last_sender.user_name,
                phone=last_sender.phone,
            )

    return last_sender, last_date


def parse_chat_date(value: str) -> Optional[date]:
    """Parse a date string into a ``date``.

    Accepts the stored ``dd/mm/yyyy`` format as well as common CLI inputs
    (``yyyy-mm-dd``, ``dd-mm-yyyy``, ``dd.mm.yyyy``). Returns ``None`` when the
    value cannot be parsed.
    """
    s = (value or "").strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None
