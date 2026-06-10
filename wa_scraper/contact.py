"""Shared contact parsing for message senders and reaction reactors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

_PHONE_SUFFIX_RE = re.compile(
    r",\s*(\+\d[\d\s\-()]{7,}\d|\d[\d\s\-()]{8,}\d)\s*$"
)
_PHONE_IN_TEXT_RE = re.compile(
    r"(\+\d[\d\s\-()]{7,}\d|\d[\d\s\-()]{8,}\d)"
)
_PHONE_ONLY_RE = re.compile(
    r"^(\+\d[\d\s\-()]{7,}\d|\d[\d\s\-()]{8,}\d)$"
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


def extract_phone(text: str) -> Optional[str]:
    match = _PHONE_IN_TEXT_RE.search(text or "")
    return match.group(1).strip() if match else None


def looks_like_phone(text: str) -> bool:
    return bool(_PHONE_ONLY_RE.match((text or "").strip()))


def contact_dedup_key(contact: Contact) -> str:
    name_key = clean_contact_name(contact.user_name).lower()
    if name_key:
        return name_key
    return (contact.phone or "").replace(" ", "")


def parse_contact(raw) -> Optional[Contact]:
    if isinstance(raw, dict):
        user_name = clean_contact_name(str(raw.get("user_name") or ""))
        phone = str(raw.get("phone") or "").strip() or None
        if not user_name and not phone:
            return None
        return Contact(user_name=user_name, phone=phone)

    text = str(raw).strip()
    if not text:
        return None
    if text == "You":
        return Contact(user_name="You", phone=None)
    if looks_like_phone(text):
        return Contact(user_name="", phone=text)
    phone = extract_phone(text)
    user_name = clean_contact_name(text)
    if user_name and looks_like_phone(user_name):
        return Contact(user_name="", phone=user_name)
    return Contact(user_name=user_name, phone=phone)


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
