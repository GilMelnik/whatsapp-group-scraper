"""Shared contact parsing for message senders and reaction reactors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

_PHONE_SUFFIX_RE = re.compile(
    r",\s*(\+\d[\d\s\-()]{7,}\d|\d[\d\s\-()]{8,}\d)\s*$"
)
_MAYBE_PREFIX_RE = re.compile(r"^Maybe\s+", re.I)


@dataclass
class Contact:
    user_name: str
    phone: Optional[str] = None


def clean_contact_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"^~\s*", "", name)
    name = _MAYBE_PREFIX_RE.sub("", name)
    name = _PHONE_SUFFIX_RE.sub("", name).strip()
    return name.strip()


def contact_dedup_key(contact: Contact) -> str:
    name_key = clean_contact_name(contact.user_name).lower()
    if name_key:
        return name_key
    return (contact.phone or "").replace(" ", "")


def dedupe_contacts(contacts: List[Contact]) -> List[Contact]:
    out: List[Contact] = []
    seen: set = set()
    for contact in contacts:
        key = contact_dedup_key(contact)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(contact)
    return out
