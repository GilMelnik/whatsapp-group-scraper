"""Data models for scraped WhatsApp messages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from wa_scraper.contact import Contact

Reactor = Contact


@dataclass
class Reaction:
    emoji:   str
    count:   int
    senders: List[Contact] = field(default_factory=list)


@dataclass
class Quote:
    sender: Contact
    text:   str


@dataclass
class Message:
    id:          str
    date:        str
    time:        str
    sender:      Contact
    text:        str
    is_outgoing: bool
    quote:       Optional[Quote]    = None
    reactions:   List[Reaction]     = field(default_factory=list)
    media_type:  Optional[str]      = None

