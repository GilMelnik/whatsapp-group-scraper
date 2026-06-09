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
    s     = raw.strip()
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

    # ── English: explicit date formats ───────────────────────────────────────
    for fmt in (
        "%d %B %Y",   # 15 March 2025
        "%B %d, %Y",  # March 15, 2025
        "%d %b %Y",   # 15 Mar 2025
        "%d/%m/%Y",   # 15/03/2025
        "%m/%d/%Y",   # 03/15/2025
    ):
        try:
            return datetime.strptime(s, fmt).strftime("%d/%m/%Y")
        except ValueError:
            pass

    return s  # return verbatim if nothing matched
