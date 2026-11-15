"""Data models for scraped WhatsApp messages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from wa_android.contact import Contact

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
class Mention:
    tag:    str  # the digits of "@<digits>" as they stand in the text
    member: str  # the tagged member's id (wa_android.members)


@dataclass
class LinkPreview:
    url:         str = ""
    title:       str = ""
    description: str = ""


@dataclass
class Message:
    id:           str
    date:         str
    time:         str
    sender:       Contact
    text:         str
    is_outgoing:  bool
    quote:        Optional[Quote]       = None
    reactions:    List[Reaction]        = field(default_factory=list)
    media_type:   Optional[str]         = None
    is_forwarded: bool                  = False
    is_deleted:   bool                  = False
    link_preview: Optional[LinkPreview] = None
    mentions:     List[Mention]         = field(default_factory=list)

