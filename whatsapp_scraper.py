#!/usr/bin/env python3
"""
whatsapp_scraper.py  — Hebrew + English aware
═══════════════════════════════════════════════════════════════════════════════
Scrapes a WhatsApp Web group chat and exports every message as JSON.
Handles messages and nicknames in Hebrew, English, or mixed RTL+LTR text.

Output format  (wa_chat.json):
────────────────────────────────
[
  {
    "id":          "false_12345678901@g.us_ABCDEF0123",
    "date":        "07/06/2025",
    "time":        "14:32",
    "sender":      "Alice",
    "text":        "See you tomorrow!",
    "is_outgoing": false,
    "quote": {
      "sender": "Bob",
      "text":   "Are you coming?"
    },
    "reactions": [
      { "emoji": "👍", "count": 2, "senders": ["Bob", "Carol"] }
    ],
    "media_type": null
  },
  ...
]

Captured fields
───────────────
  • sender       – nickname shown in WhatsApp (or "You" for outgoing messages)
  • date         – calendar date from the chat's date-separator cards
  • time         – HH:MM (or H:MM AM/PM, matching your locale)
  • text         – raw message text (empty for media-only messages)
  • is_outgoing  – true if YOU sent the message
  • quote        – sender + text of the replied-to message, or null
  • reactions    – list of { emoji, count, senders[] }
                   senders is populated only with --reactions-detail
  • media_type   – "image" | "video" | "audio" | "sticker" | "document"
                   "gif" | "location" | null

Setup
─────
  pip install selenium undetected-chromedriver

Usage
─────
  python whatsapp_scraper.py --group "My Group"
  python whatsapp_scraper.py --group "Team Chat" --output team.json
  python whatsapp_scraper.py --group "Team Chat" --reactions-detail
  python whatsapp_scraper.py --group "Team Chat" --scroll-pause 1.5

Notes
─────
  • A persistent Chrome profile is stored at ~/.wa_scraper_profile so the QR
    scan is only required once.
  • The browser window is kept visible (not headless) and all interactions use
    human-like timing and random-step scrolling.
  • WhatsApp Web uses a virtual list – rows outside the viewport are removed
    from the DOM, so messages are harvested progressively as you scroll.
  • --reactions-detail clicks every reaction pill to read who reacted; this
    is noticeably slower.  Without this flag only emoji + count are recorded.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    ElementNotInteractableException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    import undetected_chromedriver as uc
except ImportError:
    print(
        "Missing dependency.\n"
        "Run:  pip install undetected-chromedriver selenium\n"
        "Then re-run this script."
    )
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
#  Data model
# ══════════════════════════════════════════════════════════════════════════════


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


# ══════════════════════════════════════════════════════════════════════════════
#  CSS selectors
#  WhatsApp Web uses hashed class names that change often, so we rely on
#  data-testid attributes wherever possible.  If WhatsApp changes its markup
#  update only this dictionary.
# ══════════════════════════════════════════════════════════════════════════════

S: Dict[str, str] = {
    # ── login ──────────────────────────────────────────────────────────────────
    "qr_code":       '[data-testid="qrcode"]',
    "app_ready":     '[data-testid="chat-list-search"]',

    # ── search / chat list ─────────────────────────────────────────────────────
    "search_box":    '[data-testid="chat-list-search"]',
    "chat_cell":     '[data-testid="cell-frame-container"]',
    "cell_title":    '[data-testid="cell-frame-title"] span',

    # ── message panel ──────────────────────────────────────────────────────────
    "panel":         '[data-testid="conversation-panel-messages"]',
    "date_card":     '[data-testid="msg-dateseparator"]',
    "message_row":   'div[data-id]',
    "loading_spin":  '[data-testid="conv-loadingmsg"]',   # while older msgs load

    # ── per-message elements ───────────────────────────────────────────────────
    "author":        '[data-testid="author"]',
    # Direct-child (>) is essential: descendant ( ) would double-count nested
    # span[dir] elements that WhatsApp inserts for mixed Hebrew/English text.
    "msg_text":      'span.selectable-text > span[dir]',
    "msg_meta":      '[data-testid="msg-meta"]',           # time + tick icons

    # ── quoted / replied-to preview ────────────────────────────────────────────
    "quote_block":   '[data-testid="quoted-message"]',
    "quote_author":  '[data-testid="quoted-mention"]',

    # ── emoji reactions ────────────────────────────────────────────────────────
    "rxn_container": (
        '[data-testid="reactions-read-more-container"],'
        '[data-testid="reactions-container"]'
    ),
    # Dialog that opens when you click a reaction pill
    "rxn_dialog":    '[data-testid="reactions-list"]',
    "rxn_tab":       '[data-testid="reactions-tab"]',
    "rxn_name":      '[data-testid="react-participant"]',

    # ── media type indicators ──────────────────────────────────────────────────
    "m_image":    '[data-testid="image-thumb"],[data-testid="media-url-provider"]',
    "m_video":    '[data-testid="video-thumb"]',
    "m_audio":    '[data-testid="audio-player"]',
    "m_sticker":  '[data-testid="sticker-media"]',
    "m_doc":      '[data-testid="document-thumb"]',
    "m_gif":      '[data-testid="gif-thumb"]',
    "m_location": '[data-testid="location-map"]',
}

MEDIA_CHECKS: List[tuple] = [
    ("image",    S["m_image"]),
    ("video",    S["m_video"]),
    ("audio",    S["m_audio"]),
    ("sticker",  S["m_sticker"]),
    ("document", S["m_doc"]),
    ("gif",      S["m_gif"]),
    ("location", S["m_location"]),
]


# ══════════════════════════════════════════════════════════════════════════════
#  Hebrew localisation tables
#  Used by normalize_date() when WhatsApp's interface language is Hebrew.
# ══════════════════════════════════════════════════════════════════════════════

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

# ══════════════════════════════════════════════════════════════════════════════
#  JavaScript text-extraction snippets
#
#  Why JavaScript instead of Selenium CSS queries?
#
#  WhatsApp wraps every message in   span.selectable-text > span[dir="ltr"|"rtl"]
#  For a purely Hebrew message that is one span.  For mixed Hebrew+English
#  WhatsApp nests a second span[dir] inside the first:
#
#      <span dir="rtl">                       ← outer, covers whole message
#          שלום
#          <span dir="ltr">world</span>       ← inner, wraps only the LTR word
#          שלום
#      </span>
#
#  A CSS descendant selector ( ) matches BOTH spans, so Selenium's find_all
#  returns ["שלום world שלום", "world"] — the English word is duplicated.
#  A direct-child selector (>) matches only the outer span whose .innerText
#  already includes the nested English text → correct single result.
#
#  The JS snippets additionally exclude the quoted-message block, reactions,
#  and the timestamp meta-span, which all contain their own span[dir] nodes.
# ══════════════════════════════════════════════════════════════════════════════

_JS_MSG_TEXT = r"""
(function(row) {
    var skip = [
        '[data-testid="quoted-message"]',
        '[data-testid="reactions-read-more-container"]',
        '[data-testid="reactions-container"]',
        '[data-testid="msg-meta"]'
    ];
    var spans = row.querySelectorAll('span.selectable-text > span[dir]');
    var parts = [];
    outer:
    for (var i = 0; i < spans.length; i++) {
        var sp = spans[i];
        for (var j = 0; j < skip.length; j++) {
            if (sp.closest(skip[j])) continue outer;
        }
        var t = sp.innerText;
        if (t && t.trim()) parts.push(t);
    }
    return parts.join('\n');
})(arguments[0]);
"""

_JS_QUOTE_TEXT = r"""
(function(block) {
    // Prefer direct-child spans (avoids double-counting nested RTL/LTR)
    var spans = block.querySelectorAll('span.selectable-text > span[dir]');
    var parts = [];
    for (var i = 0; i < spans.length; i++) {
        var t = spans[i].innerText;
        if (t && t.trim()) parts.push(t.trim());
    }
    // Fallback: any span[dir] if the preferred selector found nothing
    if (!parts.length) {
        spans = block.querySelectorAll('span[dir]');
        for (var i = 0; i < spans.length; i++) {
            var t = spans[i].innerText;
            if (t && t.trim()) parts.push(t.trim());
        }
    }
    return parts.join(' ');
})(arguments[0]);
"""

# ══════════════════════════════════════════════════════════════════════════════
#  Utility helpers
# ══════════════════════════════════════════════════════════════════════════════


def jitter(lo: float = 0.3, hi: float = 1.0) -> None:
    """Random sleep to break up robotic timing patterns."""
    time.sleep(random.uniform(lo, hi))


def scroll_by(driver, el, px: int, step: int = 120) -> None:
    """
    Scroll a DOM element by `px` pixels in small, randomised increments
    that look like a real human scrolling with a mouse wheel.
    """
    sign      = 1 if px > 0 else -1
    remaining = abs(px)
    while remaining > 0:
        chunk = min(remaining, random.randint(step // 2, int(step * 1.6)))
        driver.execute_script(
            "arguments[0].scrollTop += arguments[1];", el, sign * chunk
        )
        remaining -= chunk
        time.sleep(random.uniform(0.012, 0.055))


def find(el, css: str):
    """Return the first matching element, or None."""
    try:
        return el.find_element(By.CSS_SELECTOR, css)
    except (NoSuchElementException, StaleElementReferenceException):
        return None


def find_all(el, css: str) -> list:
    try:
        return el.find_elements(By.CSS_SELECTOR, css)
    except (NoSuchElementException, StaleElementReferenceException):
        return []


def _extract_text(driver, row_el) -> str:
    """
    Return the message body text, correctly handling:
      • Pure Hebrew (RTL)
      • Pure English (LTR)
      • Mixed Hebrew+English in the same message
      • Messages that contain a quoted reply (quote text is excluded)

    A single JS call is used so the browser's own bidirectional text engine
    resolves the reading order — no guesswork in Python.
    Falls back to a Python implementation if JS fails.
    """
    try:
        result = driver.execute_script(_JS_MSG_TEXT, row_el)
        return (result or "").strip()
    except Exception:
        # Python fallback: direct-child selector avoids double-counting
        parts = [
            sp.text for sp in find_all(row_el, "span.selectable-text > span[dir]")
            if sp.text
        ]
        return "\n".join(parts).strip()


def normalize_date(raw: str) -> str:
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


def wait_no_spinner(driver, panel, timeout: float = 8.0) -> None:
    """Wait until the 'loading older messages' spinner disappears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not find(panel, S["loading_spin"]):
            return
        time.sleep(0.3)


# ══════════════════════════════════════════════════════════════════════════════
#  Browser / driver setup
# ══════════════════════════════════════════════════════════════════════════════


def build_driver(profile_dir: Optional[str] = None) -> uc.Chrome:
    """
    Create an undetected Chrome instance with a persistent user profile.
    A persistent profile means:
      • WhatsApp remembers the login session (no QR scan on subsequent runs).
      • Browser history / cookies look like a real returning user.
    """
    opts = uc.ChromeOptions()
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--lang=he,en-US,en")   # Hebrew first so WA date cards come in Hebrew
    opts.add_argument("--no-first-run")
    opts.add_argument("--disable-blink-features=AutomationControlled")

    pdir = (
        Path(profile_dir)
        if profile_dir
        else Path.home() / ".wa_scraper_profile"
    )
    pdir.mkdir(parents=True, exist_ok=True)
    opts.add_argument(f"--user-data-dir={pdir}")

    driver = uc.Chrome(options=opts, version_main=None)
    # Remove the "webdriver" property that some checks look for
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    return driver


# ══════════════════════════════════════════════════════════════════════════════
#  Login
# ══════════════════════════════════════════════════════════════════════════════


def wait_for_login(driver: uc.Chrome, timeout: int = 180) -> None:
    """Block until the chat-list is visible (= fully logged in)."""
    # Check for QR screen
    try:
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, S["qr_code"]))
        )
        print("┌─────────────────────────────────────────────┐")
        print("│  Scan the QR code in the browser with your  │")
        print("│  phone to log in to WhatsApp Web.           │")
        print("└─────────────────────────────────────────────┘")
    except TimeoutException:
        pass  # Already logged in – QR never appeared

    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, S["app_ready"]))
    )
    print("Session active ✓")
    jitter(1.5, 2.5)


# ══════════════════════════════════════════════════════════════════════════════
#  Chat navigation
# ══════════════════════════════════════════════════════════════════════════════


def open_chat(driver: uc.Chrome, name: str) -> bool:
    """
    Type the group name into the search box one character at a time
    (human-like) and click the first matching result.
    """
    box = driver.find_element(By.CSS_SELECTOR, S["search_box"])
    box.click()
    jitter(0.4, 0.8)

    for ch in name:
        box.send_keys(ch)
        time.sleep(random.uniform(0.04, 0.16))
    jitter(1.0, 1.8)

    cells = driver.find_elements(By.CSS_SELECTOR, S["chat_cell"])
    for cell in cells:
        titles = cell.find_elements(By.CSS_SELECTOR, S["cell_title"])
        if titles and name.lower() in titles[0].text.lower():
            cell.click()
            jitter(1.2, 2.4)
            return True

    if cells:
        print(f"  ⚠  Exact match not found – clicking first result.")
        cells[0].click()
        jitter(1.2, 2.4)
        return True

    print(f"  ✗  No chat found for '{name}'.")
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  Parsing helpers
# ══════════════════════════════════════════════════════════════════════════════


def _parse_quote(driver, row_el) -> Optional[Quote]:
    """
    Extract the quoted / replied-to message block.
    Uses JS (_JS_QUOTE_TEXT) so Hebrew, English, and mixed quote text
    are all read correctly without span double-counting.
    """
    block = find(row_el, S["quote_block"])
    if block is None:
        return None

    # Sender of the quoted message (the coloured mention span)
    author_el = find(block, S["quote_author"])
    q_sender  = author_el.text.strip() if author_el else ""

    # Text of the quoted message
    try:
        q_text = driver.execute_script(_JS_QUOTE_TEXT, block)
        q_text = (q_text or "").strip()
    except Exception:
        q_text = ""
        for sp in find_all(block, "span[dir]"):
            t = sp.text.strip()
            if t and t != q_sender:
                q_text = t
                break

    # Strip sender name if it was accidentally prepended to the text
    if q_sender and q_text.startswith(q_sender):
        q_text = q_text[len(q_sender):].strip()

    return Quote(sender=q_sender, text=q_text) if (q_sender or q_text) else None


def _parse_reactions_basic(row_el) -> List[Reaction]:
    """
    Collect emoji reactions from the inline pills without any clicks.
    Returns a list of Reaction(emoji, count, senders=[]).
    """
    results: List[Reaction] = []
    for container in find_all(row_el, S["rxn_container"]):
        try:
            spans   = container.find_elements(By.TAG_NAME, "span")
            emoji   = ""
            count   = 1
            for sp in spans:
                t = sp.text.strip()
                if not t:
                    continue
                if t.isdigit():
                    count = int(t)
                else:
                    if emoji:
                        results.append(Reaction(emoji=emoji, count=count, senders=[]))
                    emoji = t
                    count = 1
            if emoji:
                results.append(Reaction(emoji=emoji, count=count, senders=[]))
        except StaleElementReferenceException:
            pass
    return results


def _parse_reactions_detail(driver, row_el) -> List[Reaction]:
    """
    Click each reaction pill to open the 'who reacted' dialog, then harvest
    names.  Much slower – use only with --reactions-detail.
    """
    basic    = _parse_reactions_basic(row_el)
    detailed = []

    for rxn in basic:
        # Locate the pill element for this emoji
        pill_sel = (
            f'[data-testid="reactions-read-more-container"] span,'
            f'[data-testid="reactions-container"] span'
        )
        pills = find_all(row_el, pill_sel)
        target_pill = None
        for p in pills:
            if p.text.strip() == rxn.emoji:
                target_pill = p
                break

        senders: List[str] = []
        if target_pill:
            try:
                ActionChains(driver).move_to_element(target_pill).perform()
                jitter(0.3, 0.6)
                target_pill.click()
                jitter(0.5, 0.9)

                # Wait for the reaction detail dialog
                dialog = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, S["rxn_dialog"]))
                )

                # If there are emoji tabs, click the right one
                tabs = find_all(dialog, S["rxn_tab"])
                for tab in tabs:
                    if rxn.emoji in tab.text:
                        tab.click()
                        jitter(0.3, 0.6)
                        break

                # Collect names
                for name_el in find_all(dialog, S["rxn_name"]):
                    n = name_el.text.strip()
                    if n:
                        senders.append(n)

                # Close dialog with Escape
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                jitter(0.4, 0.8)

            except (TimeoutException, StaleElementReferenceException,
                    ElementNotInteractableException):
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                jitter(0.3, 0.5)

        detailed.append(Reaction(emoji=rxn.emoji, count=rxn.count, senders=senders))

    return detailed


def _parse_message(driver, row_el, current_date: str,
                   reactions_detail: bool = False) -> Optional[Message]:
    """
    Parse a single message row into a Message dataclass.
    Returns None for system-event rows (e.g. "You were added to the group").
    """
    try:
        mid = row_el.get_attribute("data-id")
        if not mid:
            return None

        # ── direction ─────────────────────────────────────────────────────────
        # data-id starts with "true_" for outgoing, "false_" for incoming
        is_out = mid.startswith("true_")

        # ── sender ────────────────────────────────────────────────────────────
        sender    = "You" if is_out else ""
        author_el = find(row_el, S["author"])
        if author_el:
            a = author_el.text.strip()
            if a:
                sender = a

        # ── timestamp ─────────────────────────────────────────────────────────
        msg_time = ""
        meta_el  = find(row_el, S["msg_meta"])
        if meta_el:
            raw = meta_el.text.strip()
            # The meta span contains "HH:MM" plus delivery tick icons
            m   = re.search(r"\d{1,2}:\d{2}(?:\s?[AaPp][Mm])?", raw)
            msg_time = m.group(0) if m else raw.split()[-1]

        # ── text ──────────────────────────────────────────────────────────────
        # _extract_text uses JS so Hebrew (RTL), English (LTR), and mixed
        # messages are all captured correctly without double-counting.
        text = _extract_text(driver, row_el)

        # ── media type ────────────────────────────────────────────────────────
        media_type = None
        if not text:
            for mtype, sel in MEDIA_CHECKS:
                if find(row_el, sel):
                    media_type = mtype
                    break

        # Skip rows with no recognisable content (group-event cards, etc.)
        if not sender and not text and media_type is None:
            return None

        # ── reactions ─────────────────────────────────────────────────────────
        reactions = (
            _parse_reactions_detail(driver, row_el)
            if reactions_detail
            else _parse_reactions_basic(row_el)
        )

        return Message(
            id          = mid,
            date        = current_date,
            time        = msg_time,
            sender      = sender,
            text        = text,
            is_outgoing = is_out,
            quote       = _parse_quote(driver, row_el),
            reactions   = reactions,
            media_type  = media_type,
        )

    except StaleElementReferenceException:
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  Main scroll + harvest loop
# ══════════════════════════════════════════════════════════════════════════════


def scrape_chat(
    driver:           uc.Chrome,
    scroll_pause:     float = 1.0,
    max_top_scrolls:  int   = 6000,
    reactions_detail: bool  = False,
) -> List[Message]:
    """
    1. Wait for the message panel.
    2. Scroll to the very beginning of the chat history.
    3. Scroll downward, harvesting messages as they appear.
    4. Stop when we have been at the bottom with no new messages for a few passes.
    """

    # ── wait for panel ────────────────────────────────────────────────────────
    panel = WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, S["panel"]))
    )

    # ── scroll all the way to the top ────────────────────────────────────────
    print("↑  Rewinding to the start of history …  (this may take a while)")
    prev_top  = -1
    stall_n   = 0
    for _ in range(max_top_scrolls):
        wait_no_spinner(driver, panel)
        top = driver.execute_script("return arguments[0].scrollTop;", panel)

        if top == 0:
            # Confirm we are truly at the top (wait for any background load)
            jitter(1.0, 1.8)
            wait_no_spinner(driver, panel)
            if driver.execute_script("return arguments[0].scrollTop;", panel) == 0:
                break

        if top == prev_top:
            # Scrolling is not moving – wait for older batch to load
            stall_n += 1
            jitter(2.0, 3.5)
            wait_no_spinner(driver, panel)
            if stall_n >= 4:
                break          # Genuinely stuck at top
        else:
            stall_n = 0

        prev_top = top
        scroll_by(driver, panel, -600)
        jitter(scroll_pause * 0.5, scroll_pause * 1.3)

    print("↓  Reading messages …")
    jitter(1.2, 2.2)

    # ── scroll down + collect ─────────────────────────────────────────────────
    messages:     Dict[str, Message] = {}
    current_date: str = datetime.today().strftime("%d/%m/%Y")  # fallback
    no_new_streak = 0

    while True:
        # Re-acquire panel in case of stale reference after a long pause
        try:
            items = panel.find_elements(
                By.CSS_SELECTOR,
                f'{S["date_card"]}, {S["message_row"]}'
            )
        except StaleElementReferenceException:
            panel = driver.find_element(By.CSS_SELECTOR, S["panel"])
            items = panel.find_elements(
                By.CSS_SELECTOR,
                f'{S["date_card"]}, {S["message_row"]}'
            )

        before = len(messages)

        for item in items:
            try:
                # ── date-separator card ───────────────────────────────────────
                testid = item.get_attribute("data-testid") or ""
                if testid == "msg-dateseparator":
                    raw = item.text.strip()
                    if raw:
                        current_date = normalize_date(raw)
                    continue

                # ── date separator nested inside a data-id row ────────────────
                nested = find(item, S["date_card"])
                if nested:
                    raw = nested.text.strip()
                    if raw:
                        current_date = normalize_date(raw)
                    continue   # row only contains the date card, no message

                # ── message row ───────────────────────────────────────────────
                msg = _parse_message(driver, item, current_date, reactions_detail)
                if msg and msg.id not in messages:
                    messages[msg.id] = msg

            except StaleElementReferenceException:
                continue

        added        = len(messages) - before
        no_new_streak = 0 if added else no_new_streak + 1

        # ── check if we're at the bottom ──────────────────────────────────────
        scroll_top    = driver.execute_script("return arguments[0].scrollTop;",    panel)
        scroll_height = driver.execute_script("return arguments[0].scrollHeight;", panel)
        client_height = driver.execute_script("return arguments[0].clientHeight;", panel)
        at_bottom     = (scroll_top + client_height) >= (scroll_height - 20)

        if at_bottom and no_new_streak >= 4:
            print(f"\n✓  End of chat reached.  {len(messages)} messages captured.")
            break

        scroll_by(driver, panel, random.randint(380, 720))
        jitter(scroll_pause * 0.6, scroll_pause * 1.4)

        if added and len(messages) % 50 == 0:
            print(f"  {len(messages)} messages collected …", end="\r", flush=True)

    return list(messages.values())


# ══════════════════════════════════════════════════════════════════════════════
#  CLI entry point
# ══════════════════════════════════════════════════════════════════════════════


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export a WhatsApp Web group chat to JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--group", required=True,
        help="Group name to scrape (partial/case-insensitive match).",
    )
    ap.add_argument(
        "--output", default="wa_chat.json",
        help="Output JSON file path (default: wa_chat.json).",
    )
    ap.add_argument(
        "--profile-dir", default=None,
        help="Custom Chrome profile directory (default: ~/.wa_scraper_profile).",
    )
    ap.add_argument(
        "--scroll-pause", type=float, default=1.0,
        help="Base delay (seconds) between scroll steps (default: 1.0).",
    )
    ap.add_argument(
        "--max-top-scrolls", type=int, default=6000,
        help="Max upward scroll attempts when rewinding to top (default: 600).",
    )
    ap.add_argument(
        "--reactions-detail", action="store_true",
        help="Click each reaction pill to record who reacted (slower).",
    )
    args = ap.parse_args()

    driver = build_driver(args.profile_dir)
    try:
        driver.get("https://web.whatsapp.com/")
        wait_for_login(driver)

        print(f"Opening chat: {args.group!r}")
        if not open_chat(driver, args.group):
            print("Could not open the group.  Exiting.")
            sys.exit(1)

        messages = scrape_chat(
            driver,
            scroll_pause     = args.scroll_pause,
            max_top_scrolls  = args.max_top_scrolls,
            reactions_detail = args.reactions_detail,
        )

        output_path = Path(args.output)
        payload     = [asdict(m) for m in messages]
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"✓  Saved {len(messages)} messages → {output_path}")

    finally:
        input("\nPress Enter to close the browser …")
        driver.quit()


if __name__ == "__main__":
    main()
