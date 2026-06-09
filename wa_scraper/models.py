"""Data models for scraped WhatsApp messages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Reaction:
    emoji:   str
    count:   int
    senders: List[str] = field(default_factory=list)


@dataclass
class Quote:
    sender: str
    text:   str


@dataclass
class Message:
    id:          str
    date:        str
    time:        str
    sender:      str
    text:        str
    is_outgoing: bool
    quote:       Optional[Quote]    = None
    reactions:   List[Reaction]     = field(default_factory=list)
    media_type:  Optional[str]      = None

