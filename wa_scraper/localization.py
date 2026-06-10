"""Date normalization for Hebrew and English WhatsApp UI."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Dict

# Hebrew weekday names → Python datetime.weekday() index
# (In Israel, the week starts on Sunday, index 6 in Python's Monday=0 scheme)
_HE_WEEKDAYS: Dict[str, int] = {
    "ראשון":  6,   # Sunday
    "שני":    0,   # Monday
    "שלישי":  1,   # Tuesday
    "רביעי":  2,   # Wednesday
    "חמישי":  3,   # Thursday
    "שישי":   4,   # Friday
    "שבת":    5,   # Saturday
}

# Hebrew month names → month number  (two common spellings for March)
_HE_MONTHS: Dict[str, int] = {
    "ינואר":   1,
    "פברואר":  2,
    "מרץ":     3,
    "מרס":     3,
    "אפריל":   4,
    "מאי":     5,
    "יוני":    6,
    "יולי":    7,
    "אוגוסט":  8,
    "ספטמבר":  9,
    "אוקטובר": 10,
    "נובמבר":  11,
    "דצמבר":   12,
}



_SYSTEM_DATE_PATTERNS = (
    re.compile(r"\+\d[\d\s\-()]{6,}\d"),
    re.compile(r"(?<!\d)\d[\d\s\-()]{7,}\d(?!\d)"),
    re.compile(r"\badded\b", re.I),
    re.compile(r"\bwas added\b", re.I),
    re.compile(r"\bwere added\b", re.I),
    re.compile(r"\bjoined\b", re.I),
    re.compile(r"\binvite link\b", re.I),
    re.compile(r"\bremoved\b", re.I),
    re.compile(r"\bwas removed\b", re.I),
    re.compile(r"\bturned on\b", re.I),
    re.compile(r"\bturned off\b", re.I),
    re.compile(r"\bdisappearing messages\b", re.I),
    re.compile(r"\badmins?\b", re.I),
    re.compile(r"\brecalled\b", re.I),
    re.compile(r"\bdeleted\b", re.I),
    re.compile(r"\bleft\b", re.I),
    re.compile(r"\bcreated\b", re.I),
    re.compile(r"\bchanged\b", re.I),
    re.compile(r"\bsecurity code\b", re.I),
    re.compile(r"\bforwarded\b", re.I),
)


def is_date_label(raw: str) -> bool:
    """Return True only for WhatsApp in-chat day/date separator labels."""
    s = raw.strip()
    if not s or len(s) > 35 or "\n" in s:
        return False
    if re.match(r"^\d{1,2}:\d{2}", s):
        return False
    for pat in _SYSTEM_DATE_PATTERNS:
        if pat.search(s):
            return False

    lc = s.lower()
    if lc in ("today", "yesterday"):
        return True

    if lc in (
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday",
    ):
        return True

    if s in ("היום", "אתמול", *_HE_WEEKDAYS.keys()):
        return True

    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?$", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        as_md = 1 <= a <= 12 and 1 <= b <= 31
        as_dm = 1 <= a <= 31 and 1 <= b <= 12
        return as_md or as_dm

    if re.match(r"^\d{1,2}\s+\S+\s+\d{4}$", s):
        return True

    if re.match(r"^[A-Za-z\u0590-\u05FF]+\s+\d{1,2},?\s+\d{4}$", s):
        return True

    return False


def normalize_date_impl(raw: str) -> str:
    """
    Convert WhatsApp date-separator labels to dd/mm/yyyy.

    Handles English labels:
        "Today", "Yesterday", weekday names, "15 March 2025", "03/15/2025" …

    Handles Hebrew labels (when WhatsApp UI language is Hebrew):
        "היום"       → today
        "אתמול"      → yesterday
        "ראשון"…"שבת" → most-recent matching weekday
        "15 בינואר 2025" or "15 ינואר 2025" → dd/mm/yyyy
    """
    s = raw.strip()
    if not is_date_label(s):
        return ""

    today = date.today()
    lc    = s.lower()

    # ── English: named labels ─────────────────────────────────────────────────
    if lc == "today":
        return today.strftime("%d/%m/%Y")
    if lc == "yesterday":
        return (today - timedelta(days=1)).strftime("%d/%m/%Y")

    EN_WEEKDAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    if lc in EN_WEEKDAYS:
        idx = EN_WEEKDAYS.index(lc)
        for delta in range(1, 8):
            d = today - timedelta(days=delta)
            if d.weekday() == idx:
                return d.strftime("%d/%m/%Y")

    # ── Hebrew: named labels ──────────────────────────────────────────────────
    if s in ("היום",):          # "today" in Hebrew
        return today.strftime("%d/%m/%Y")
    if s in ("אתמול",):         # "yesterday" in Hebrew
        return (today - timedelta(days=1)).strftime("%d/%m/%Y")

    if s in _HE_WEEKDAYS:
        idx = _HE_WEEKDAYS[s]
        for delta in range(1, 8):
            d = today - timedelta(days=delta)
            if d.weekday() == idx:
                return d.strftime("%d/%m/%Y")

    # ── Hebrew: explicit date, e.g. "15 בינואר 2025" or "15 ינואר 2025" ──────
    # The preposition "ב" (= "in") optionally precedes the month name.
    m = re.match(r'^(\d{1,2})\s+ב?(\S+)\s+(\d{4})$', s)
    if m:
        month_word = m.group(2)
        if month_word in _HE_MONTHS:
            day  = int(m.group(1))
            mon  = _HE_MONTHS[month_word]
            year = int(m.group(3))
            return f"{day:02d}/{mon:02d}/{year}"

    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?$", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        year_raw = m.group(3)
        if year_raw:
            year = int(year_raw)
            if len(year_raw) == 2:
                year += 2000 if year < 100 else 0
        else:
            year = today.year

        parsed: list[date] = []
        if 1 <= a <= 12 and 1 <= b <= 31:
            try:
                parsed.append(date(year, a, b))
            except ValueError:
                pass
        if 1 <= b <= 12 and 1 <= a <= 31 and (b, a) != (a, b):
            try:
                parsed.append(date(year, b, a))
            except ValueError:
                pass
        if len(parsed) == 1:
            return parsed[0].strftime("%d/%m/%Y")
        if len(parsed) == 2:
            # WhatsApp Web often renders US-style M/D/Y (e.g. 5/28/2026).
            return parsed[0].strftime("%d/%m/%Y")

    # ── English: explicit date formats ───────────────────────────────────────
    for fmt in (
        "%d %B %Y",   # 15 March 2025
        "%B %d, %Y",  # March 15, 2025
        "%d %b %Y",   # 15 Mar 2025
        "%m/%d/%Y",   # 03/15/2025
        "%d/%m/%Y",   # 15/03/2025
        "%d.%m.%Y",   # 15.03.2025
    ):
        try:
            return datetime.strptime(s, fmt).strftime("%d/%m/%Y")
        except ValueError:
            pass

    return ""
