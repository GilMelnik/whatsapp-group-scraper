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
  • --reactions-detail clicks reaction pills during the downward scroll to
    record who reacted; this is noticeably slower than emoji + count only.
  • The scraper performs one upward scroll to reach history start, then one
    downward scroll that harvests everything currently in the DOM.
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

# WhatsApp updates its DOM often; try selectors in order until one matches.
APP_READY_SELECTORS: List[str] = [
    '[data-testid="chat-list-search-container"]',
    '[data-testid="chat-list"]',
    '[data-testid="chat-list-search"]',          # legacy
    '#side input[data-tab="3"]',
    'div[contenteditable="true"][data-tab="3"]', # legacy
]

QR_CODE_SELECTORS: List[str] = [
    '[data-testid="qrcode"]',
    'canvas[aria-label*="QR"]',
    '[data-testid="intro-md-beta-logo-dark"]',
]

SEARCH_INPUT_SELECTORS: List[str] = [
    '[data-testid="chat-list-search-container"] input[type="text"]',
    'input[data-testid="search-input"]',
    '[data-testid="chat-list-search"] input[type="text"]',
    '#side input[data-tab="3"]',
    'input[aria-label="Search or start a new chat"]',
    'input[aria-label="Search or start new chat"]',
    'div[contenteditable="true"][data-tab="3"]',  # legacy
    '[data-testid="chat-list-search"]',         # legacy container
]

# Search-result rows live inside this panel — not the main chat list behind it.
SEARCH_RESULTS_ROOT_SELECTORS: List[str] = [
    '#pane-side [aria-label*="Search results"]',
    '#pane-side [aria-label*="search results"]',
    '#pane-side [aria-label*="תוצאות"]',          # Hebrew UI
    '[data-testid="chat-list"]',
    '#pane-side',
]

CONV_HEADER_TITLE_SELECTORS: List[str] = [
    '#main header [data-testid="conversation-info-header-chat-title"] span[title]',
    '#main header [data-testid="conversation-info-header-chat-title"] span',
    '#main header [data-testid="conversation-info-header"] span[title]',
]

COMPOSE_BOX_SELECTORS: List[str] = [
    '[data-testid="conversation-compose-box-input"]',
    '#main footer div[contenteditable="true"]',
    'footer div[contenteditable="true"][data-tab="10"]',
]

S: Dict[str, str] = {
    # ── login ──────────────────────────────────────────────────────────────────
    "qr_code":       '[data-testid="qrcode"]',
    "app_ready":     '[data-testid="chat-list-search-container"]',

    # ── search / chat list ─────────────────────────────────────────────────────
    "search_box":    '[data-testid="chat-list-search-container"] input[type="text"]',
    "chat_cell":     '[data-testid="cell-frame-container"]',
    "cell_title":    '[data-testid="cell-frame-title"] span',

    # ── message panel ──────────────────────────────────────────────────────────
    "panel":         '[data-testid="conversation-panel-messages"]',
    "date_card":     '[data-testid="msg-dateseparator"]',
    "message_row":   '[data-testid="msg-container"]',
    "loading_spin":  '[data-testid="conv-loadingmsg"]',   # while older msgs load

    # ── per-message elements ───────────────────────────────────────────────────
    # WhatsApp uses testid="author" (non-standard) as well as data-testid="author"
    "author":        '[data-testid="author"], [testid="author"]',
    "msg_text":      '[data-testid="selectable-text"]:not(.quoted-mention), span.copyable-text:not(.quoted-mention)',
    "msg_meta":      '[data-testid="msg-meta"]',
    "tail_out":      '[data-testid="tail-out"]',
    "tail_in":       '[data-testid="tail-in"]',

    # ── quoted / replied-to preview ────────────────────────────────────────────
    "quote_block":   '[data-testid="quoted-message"], [data-testid="quoted-msg"]',
    "quote_author":  '[data-testid="quoted-author"], [testid="author"], [data-testid="author"]',
    "quote_text":    '[data-testid="selectable-text"].quoted-mention, .quoted-mention',

    # ── emoji reactions ────────────────────────────────────────────────────────
    "rxn_container": (
        '[data-testid="addon-bubble-container"],'
        '[data-testid="reactions-read-more-container"],'
        '[data-testid="reactions-container"],'
        '[data-testid="msg-reactions"]'
    ),
    "rxn_dialog":    '[data-testid="reactions-list"]',
    "rxn_tab":       '[data-testid="reactions-tab"]',
    "rxn_name":      (
        '[data-testid="react-participant"],'
        '[data-testid="reactions-list-item"],'
        '[data-testid="cell-frame-title"]'
    ),
    "rxn_row":       (
        '[data-testid="react-participant"],'
        '[data-testid="reactions-list-item"],'
        '[role="listitem"],'
        '[data-testid="cell-frame-container"]'
    ),
    "rxn_pill":      (
        '[data-testid="reaction-bubble"],'
        '[data-testid="reactions-bubble"] [role="button"],'
        '[data-testid="reactions-container"] [role="button"],'
        '[data-testid="msg-reactions"] [role="button"]'
    ),
    "rxn_scroll":    '[data-testid="scrollable-content"], [data-scrolltraceid]',

    # ── media type indicators ──────────────────────────────────────────────────
    "m_image":    '[data-testid="image-thumb"],[data-testid="media-url-provider"]',
    "m_video":    '[data-testid="video-thumb"]',
    "m_audio":    '[data-testid="audio-player"]',
    "m_sticker":  '[data-testid="sticker-media"]',
    "m_doc":      '[data-testid="document-thumb"]',
    "m_gif":      '[data-testid="gif-thumb"],[data-testid="image-thumb-gif"]',
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

# Date separators plus every row shape WhatsApp uses in the virtual list.
CHAT_ITEM_SELECTOR = (
    f'{S["date_card"]}, {S["message_row"]}, '
    'div.focusable-list-item[data-id]'
)


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
#  JavaScript DOM extraction
#
#  WhatsApp Web renders each message as a tree of separate divs:
#    msg-container → author | quoted-message | body text | msg-meta | reactions
#
#  Each field is read ONLY from its own zone — never by parsing combined
#  innerText of the whole row.  Class-name selectors are avoided (they change
#  every release); data-testid attributes are used wherever possible.
# ══════════════════════════════════════════════════════════════════════════════

# Zones whose text must never bleed into the message body.
# Note: author selectors are intentionally omitted here — the message author
# often wraps or sits near the body zone; excluding [testid="author"]
# ancestors was causing every body to come back empty.
_JS_SKIP_ZONES = r"""[
    '[data-testid="quoted-message"]',
    '[data-testid="quoted-msg"]',
    '[data-testid="addon-bubble-container"]',
    '[data-testid="reaction-bubble"]',
    '[data-testid="reactions-read-more-container"]',
    '[data-testid="reactions-container"]',
    '[data-testid="msg-reactions"]',
    '[data-testid="reactions-bubble"]',
    '[data-testid="reactions-list"]',
    '[data-testid="msg-meta"]',
    '[data-testid="image-thumb"]',
    '[data-testid="video-thumb"]',
    '[data-testid="audio-player"]',
    '[data-testid="sticker-media"]',
    '[data-testid="document-thumb"]',
    '[data-testid="location-map"]',
    '.quoted-mention',
    '.wds-icon'
]"""

_JS_HELPERS = r"""
function _waInside(el, selectors) {
    for (var i = 0; i < selectors.length; i++) {
        if (el.closest(selectors[i])) return true;
    }
    return false;
}
function _waMsgRoot(row) {
    return row.querySelector('[data-testid="msg-container"]') || row;
}
function _waRowRoot(row) {
    return row.closest('.focusable-list-item') || row;
}
function _waElText(el) {
    if (!el) return '';
    var text = (el.innerText || el.textContent || '').trim();
    if (text) return text;
    if (el.tagName === 'IMG') {
        return (el.getAttribute('data-plain-text') || el.getAttribute('alt') || '').trim();
    }
    return '';
}
function _waIsBodyTextEl(el, quoteBlock) {
    if (!el) return false;
    if (quoteBlock && quoteBlock.contains(el)) return false;
    if (_waIsQuotedMention(el)) return false;
    if (el.getAttribute('testid') === 'author') return false;
    if (el.closest('[testid="author"], [data-testid="author"], [data-testid="quoted-author"]')) return false;
    return true;
}
function _waExtractBodyAfterQuote(quoteBlock) {
    if (!quoteBlock) return '';
    var anchor = quoteBlock;
    for (var depth = 0; depth < 8 && anchor; depth++) {
        var sib = anchor.nextElementSibling;
        while (sib) {
            var st = sib.querySelector('[data-testid="selectable-text"]');
            if (st && _waIsBodyTextEl(st, quoteBlock)) {
                var t = _waElText(st);
                if (_waTextOk(t)) return t;
            }
            var raw = _waElText(sib);
            if (_waTextOk(raw)) return raw;
            sib = sib.nextElementSibling;
        }
        if (anchor.parentElement === anchor) break;
        anchor = anchor.parentElement;
    }
    return '';
}
function _waExtractBodyFromAkbu(container, quoteBlock) {
    var zones = container.querySelectorAll('div._akbu, [class*="_akbu"]');
    for (var i = 0; i < zones.length; i++) {
        var zone = zones[i];
        if (quoteBlock && quoteBlock.contains(zone)) continue;
        var st = zone.querySelector('[data-testid="selectable-text"]');
        if (st && _waIsBodyTextEl(st, quoteBlock)) {
            var t = _waElText(st);
            if (_waTextOk(t)) return t;
        }
        var raw = _waElText(zone);
        if (_waTextOk(raw)) return raw;
    }
    return '';
}
function _waIsOutgoing(row) {
    var dataId = row.getAttribute('data-id') || '';
    if (dataId.indexOf('true_') === 0) return true;
    if (row.querySelector('[data-testid="tail-out"]')) return true;
    if (row.classList.contains('message-out')) return true;
    if (row.querySelector('.message-out')) return true;
    if (row.closest('.message-out')) return true;
    return false;
}
function _waLooksLikeTimestamp(s) {
    s = (s || '').trim();
    return /^\d{1,2}:\d{2}(\s?[AaPp][Mm])?$/.test(s);
}
function _waLooksLikeStatusGlyph(s) {
    s = (s || '').trim();
    return s.length > 0 && s.length <= 2 && /^[\u2713\u2714\u{1F507}\u{1F508}\u{1F509}]+$/u.test(s);
}
function _waLooksLikeIconLigature(s) {
    s = (s || '').trim();
    if (!s) return true;
    if (s.indexOf('wds-ic-') === 0 || s.indexOf('wds-icon') === 0) return true;
    // Hyphenated icon tokens only — plain words like "hello" are real text.
    if (/\s/.test(s) || !/^[a-z0-9_-]+$/.test(s)) return false;
    return s.indexOf('-') >= 0 && s.length > 3;
}
function _waParsePrePlain(row) {
    var el = row.querySelector('[data-pre-plain-text]');
    if (!el) return null;
    var pre = el.getAttribute('data-pre-plain-text') || '';
    var m = pre.match(/^\[([^\]]+)\]\s*(.+?):\s*$/);
    if (!m) return null;
    return { time: m[1].trim(), sender: m[2].trim() };
}
function _waFindQuoteBlock(row) {
    var root = _waMsgRoot(row);
    return root.querySelector('[data-testid="quoted-message"]')
        || root.querySelector('[data-testid="quoted-msg"]');
}
function _waTextOk(s) {
    s = (s || '').trim();
    return s && !_waLooksLikeTimestamp(s) && !_waLooksLikeStatusGlyph(s) && !_waLooksLikeIconLigature(s);
}
function _waDedupeNested(items) {
    var outer = [];
    for (var a = 0; a < items.length; a++) {
        var nested = false;
        for (var b = 0; b < items.length; b++) {
            if (a !== b && items[b].el !== items[a].el && items[b].el.contains(items[a].el)) {
                nested = true;
                break;
            }
        }
        if (!nested) outer.push(items[a].txt);
    }
    return outer;
}
function _waCollectTextNodes(root, skip, excludeEl) {
    var parts = [];
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var n;
    while ((n = walker.nextNode())) {
        var parent = n.parentElement;
        if (!parent) continue;
        if (excludeEl && excludeEl.contains(parent)) continue;
        if (_waInside(parent, skip)) continue;
        if (parent.closest('.wds-icon')) continue;
        var raw = (n.textContent || '').trim();
        if (!_waTextOk(raw)) continue;
        parts.push(raw);
    }
    return parts.join(' ').trim();
}
function _waIsQuotedMention(el) {
    return el && el.classList && el.classList.contains('quoted-mention');
}
function _waFindMsgAuthor(container, quoteBlock) {
    var els = container.querySelectorAll('[data-testid="author"], [testid="author"]');
    for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (quoteBlock && quoteBlock.contains(el)) continue;
        var t = (el.innerText || '').trim();
        if (t) return t;
    }
    return '';
}
function _waFindQuoteAuthor(block) {
    var el = block.querySelector('[data-testid="quoted-author"]')
        || block.querySelector('[testid="author"]')
        || block.querySelector('[data-testid="author"]');
    return el ? (el.innerText || '').trim() : '';
}
function _waCollectBodyElements(root, quoteBlock, skip) {
    var results = [];
    var els = root.querySelectorAll('[data-testid="selectable-text"]');
    for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (!_waIsBodyTextEl(el, quoteBlock)) continue;
        if (_waInside(el, skip)) continue;
        var txt = _waElText(el);
        if (!_waTextOk(txt)) continue;
        results.push({ el: el, txt: txt });
    }
    if (results.length) return _waDedupeNested(results);

    els = root.querySelectorAll(
        '[data-testid="conversation-text"], span.selectable-text, span.copyable-text, span[dir], div[dir]'
    );
    for (var j = 0; j < els.length; j++) {
        el = els[j];
        if (!_waIsBodyTextEl(el, quoteBlock)) continue;
        if (_waInside(el, skip)) continue;
        txt = _waElText(el);
        if (!_waTextOk(txt)) continue;
        results.push({ el: el, txt: txt });
    }
    return _waDedupeNested(results);
}
function _waExtractQuote(block) {
    var sender = _waFindQuoteAuthor(block);
    var text = '';

    var textEl = block.querySelector('[data-testid="selectable-text"].quoted-mention')
        || block.querySelector('.quoted-mention[data-testid="selectable-text"]')
        || block.querySelector('.quoted-mention');
    if (textEl) text = _waElText(textEl);

    if (!text) {
        var els = block.querySelectorAll('[data-testid="selectable-text"]');
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            if (el.getAttribute('testid') === 'author') continue;
            if (el.closest('[testid="author"], [data-testid="author"], [data-testid="quoted-author"]')) continue;
            var t = _waElText(el);
            if (_waTextOk(t) && t !== sender) { text = t; break; }
        }
    }

    if (!text) {
        text = _waElText(block);
        if (sender && text.indexOf(sender) === 0) {
            text = text.slice(sender.length).trim();
        }
    }
    if (!sender && !text) return null;
    return { sender: sender, text: text };
}
function _waExtractBody(container, quoteBlock, authorEl, skip) {
    // 1 — WhatsApp body wrapper (div._akbu) sitting beside the quote preview
    var fromAkbu = _waExtractBodyFromAkbu(container, quoteBlock);
    if (fromAkbu) return fromAkbu;

    // 2 — sibling walk upward from quoted-message (quote wrapper → div._akbu)
    if (quoteBlock) {
        var fromSibling = _waExtractBodyAfterQuote(quoteBlock);
        if (fromSibling) return fromSibling;
    }

    // 3 — any selectable-text outside the quote block (not .quoted-mention)
    var parts = _waCollectBodyElements(container, quoteBlock, skip);
    if (parts.length) return parts.join('\n');

    // 4 — text nodes inside copyable wrapper, excluding quote subtree
    var copyables = container.querySelectorAll('[data-pre-plain-text], .copyable-text');
    for (var c = 0; c < copyables.length; c++) {
        var copy = copyables[c];
        if (_waInside(copy, skip)) continue;
        if (quoteBlock && quoteBlock.contains(copy)) continue;
        var fromCopy = _waCollectTextNodes(copy, skip, quoteBlock);
        if (fromCopy) return fromCopy;
    }

    // 5 — walk msg-container text nodes, skip quote/meta/reactions
    var fromRoot = _waCollectTextNodes(container, skip, quoteBlock);
    if (fromRoot) return fromRoot;

    return '';
}
function _waExtractSender(row, container, isOut, quoteBlock) {
    if (isOut) return 'You';

    var sender = _waFindMsgAuthor(container, quoteBlock);
    if (sender) return sender;

    var pre = _waParsePrePlain(row);
    if (pre && pre.sender) return pre.sender;

    return '';
}
function _waExtractTime(container) {
    var meta = container.querySelector('[data-testid="msg-meta"]');
    if (!meta) return '';
    var raw = (meta.innerText || meta.textContent || '').trim();
    var m = raw.match(/\d{1,2}:\d{2}(?:\s?[AaPp][Mm])?/);
    return m ? m[0] : raw.split(/\s+/).pop();
}
"""

_JS_PARSE_MESSAGE = (
    _JS_HELPERS
    + r"""
return (function(row) {
    var SKIP = """
    + _JS_SKIP_ZONES
    + r""";
    var container = _waMsgRoot(row);
    var isOut = _waIsOutgoing(row);
    var quoteBlock = _waFindQuoteBlock(row);
    var authorEl = container.querySelector('[testid="author"], [data-testid="author"]');

    var quote = quoteBlock ? _waExtractQuote(quoteBlock) : null;
    var sender = _waExtractSender(row, container, isOut, quoteBlock);
    var text = _waExtractBody(container, quoteBlock, authorEl, SKIP);
    var time = _waExtractTime(container);

    if (!time) {
        var pre = _waParsePrePlain(row);
        if (pre && pre.time) time = pre.time;
    }

    return {
        sender: sender,
        text: text,
        time: time,
        isOutgoing: isOut,
        quote: quote
    };
})(arguments[0]);
"""
)

_JS_MSG_TEXT = (
    _JS_HELPERS
    + r"""
return (function(row) {
    var SKIP = """
    + _JS_SKIP_ZONES
    + r""";
    var container = _waMsgRoot(row);
    var quoteBlock = _waFindQuoteBlock(row);
    var authorEl = container.querySelector('[testid="author"], [data-testid="author"]');
    return _waExtractBody(container, quoteBlock, authorEl, SKIP);
})(arguments[0]);
"""
)

_JS_QUOTE_PARSE = (
    _JS_HELPERS
    + r"""
return (function(row) {
    var block = _waFindQuoteBlock(row);
    if (!block) return null;
    return _waExtractQuote(block);
})(arguments[0]);
"""
)

_JS_SENDER = (
    _JS_HELPERS
    + r"""
return (function(row) {
    var container = _waMsgRoot(row);
    var isOut = _waIsOutgoing(row);
    var quoteBlock = _waFindQuoteBlock(row);
    return _waExtractSender(row, container, isOut, quoteBlock);
})(arguments[0]);
"""
)

_JS_REACTIONS = (
    _JS_HELPERS
    + r"""
return (function(row) {
    var results = [];
    var seen = {};

    function add(emoji, count, el) {
        if (!emoji || seen[emoji]) return;
        seen[emoji] = true;
        results.push({ emoji: emoji, count: count || 1, el: el || null });
    }

    function firstEmoji(s) {
        var m = (s || '').match(/\p{Extended_Pictographic}/u);
        return m ? m[0] : '';
    }

    function parseCount(s) {
        var m = (s || '').match(/\d+/);
        return m ? parseInt(m[0], 10) : 1;
    }

    function scanReactionBubbles(root) {
        if (!root) return;
        var bubbles = root.querySelectorAll('[data-testid="reaction-bubble"]');
        for (var b = 0; b < bubbles.length; b++) {
            var btn = bubbles[b];
            var emoji = '';
            var lbl = btn.getAttribute('aria-label') || '';
            var em = firstEmoji(lbl);
            if (em) emoji = em;
            if (!emoji) {
                var img = btn.querySelector('[data-testid="reaction-bubble-item"] img[alt], img[alt]');
                if (img) emoji = (img.getAttribute('alt') || '').trim();
            }
            if (emoji) add(emoji, 1, btn);
        }
    }

    function scanRoot(root) {
        if (!root) return;
        scanReactionBubbles(root);
        var containers = root.querySelectorAll(
            '[data-testid="reactions-read-more-container"],' +
            '[data-testid="reactions-container"],' +
            '[data-testid="msg-reactions"],' +
            '[data-testid="reactions-bubble"]'
        );
        for (var c = 0; c < containers.length; c++) {
            var container = containers[c];
            var pills = container.querySelectorAll('[role="button"], button');
            for (var p = 0; p < pills.length; p++) {
                var pill = pills[p];
                var txt = (pill.innerText || pill.textContent || '').trim();
                if (!txt) continue;
                var emoji = firstEmoji(txt);
                if (!emoji) continue;
                var count = parseCount(txt.replace(emoji, ''));
                if (count < 1) count = 1;
                add(emoji, count, pill);
            }
        }
    }

    scanRoot(row);
    scanRoot(_waRowRoot(row));
    if (row.parentElement) scanRoot(row.parentElement);
    var container = row.querySelector('[data-testid="msg-container"]');
    if (container && container.parentElement) scanRoot(container.parentElement);

    return results;
})(arguments[0]);
"""
)

_JS_COLLECT_REACTORS = r"""
return (function(dialog) {
    var names = [];
    var seen = {};

    function isTab(el) {
        return !!(el && el.closest(
            '[data-testid="reactions-tab"], [role="tab"], [role="tablist"]'
        ));
    }

    function addName(n) {
        n = (n || '').trim();
        if (!n || seen[n]) return;
        if (/^\d+$/.test(n)) return;
        if (/^(All|הכל)$/i.test(n)) return;
        if (/^[\p{Extended_Pictographic}\p{Emoji}\u200d\uFE0F\s·•]+$/u.test(n)) return;
        seen[n] = true;
        names.push(n);
    }

    function nameFromRow(el) {
        if (!el || isTab(el)) return;
        var picks = el.querySelectorAll(
            '[data-testid="react-participant"],' +
            '[data-testid="cell-frame-title"],' +
            'span[title], span[dir="auto"], span[dir="ltr"], span[dir="rtl"]'
        );
        for (var i = 0; i < picks.length; i++) {
            var pick = picks[i];
            if (isTab(pick)) continue;
            var n = (pick.getAttribute('title') || pick.innerText || pick.textContent || '').trim();
            if (n && n.length < 120) { addName(n); return; }
        }
        var lines = (el.innerText || el.textContent || '').split('\n');
        for (var j = 0; j < lines.length; j++) {
            var line = lines[j].trim();
            if (line) { addName(line); return; }
        }
    }

    function collect(root) {
        if (!root) return;
        var rows = root.querySelectorAll(
            '[data-testid="react-participant"],' +
            '[data-testid="reactions-list-item"],' +
            '[role="listitem"],' +
            '[data-testid="cell-frame-container"]'
        );
        for (var i = 0; i < rows.length; i++) {
            nameFromRow(rows[i]);
        }
    }

    var scrollEl = dialog.querySelector('[data-testid="scrollable-content"]')
        || dialog.querySelector('[data-scrolltraceid]')
        || dialog.querySelector('[role="list"]')
        || dialog;
    collect(scrollEl);
    var lastTop = -1;
    for (var attempt = 0; attempt < 40; attempt++) {
        scrollEl.scrollTop += 180;
        if (scrollEl.scrollTop === lastTop) break;
        lastTop = scrollEl.scrollTop;
        collect(scrollEl);
    }
    return names;
})(arguments[0]);
"""

# ══════════════════════════════════════════════════════════════════════════════
#  Utility helpers
# ══════════════════════════════════════════════════════════════════════════════


def log(msg: str) -> None:
    """Print immediately so progress is visible when stdout is piped/buffered."""
    print(msg, flush=True)


def jitter(lo: float = 0.3, hi: float = 1.0) -> None:
    """Random sleep to break up robotic timing patterns."""
    time.sleep(random.uniform(lo, hi))


def wait_for_any_selector(
    driver,
    selectors: List[str],
    timeout: float,
    condition=EC.presence_of_element_located,
):
    """Return the first element matched by any selector within timeout."""
    deadline = time.time() + timeout
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for sel in selectors:
            try:
                return WebDriverWait(driver, 0.5).until(
                    condition((By.CSS_SELECTOR, sel))
                )
            except TimeoutException as exc:
                last_err = exc
        time.sleep(0.25)
    raise TimeoutException(
        f"Timed out waiting for any of: {', '.join(selectors)}"
    ) from last_err


def find_search_input(driver) -> Optional[object]:
    """Locate the real search input (WhatsApp moved it inside a container div)."""
    for sel in SEARCH_INPUT_SELECTORS:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if not els:
            continue
        el = els[0]
        if el.tag_name.lower() == "input":
            return el
        inner = find(el, 'input[type="text"]') or find(el, "input")
        if inner:
            return inner
        if el.get_attribute("contenteditable") == "true":
            return el
    return None


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


def _normalize_emoji(value: str) -> str:
    """Strip variation selectors so ❤ and ❤️ compare equal."""
    if not value:
        return ""
    return re.sub(r"[\uFE0E\uFE0F\u200D]", "", value)


def _emoji_matches(left: str, right: str) -> bool:
    """Return True when two strings refer to the same reaction emoji."""
    left_n  = _normalize_emoji(left)
    right_n = _normalize_emoji(right)
    if not left_n or not right_n:
        return False
    return left_n in right_n or right_n in left_n


def _wait_for_reaction_dialog(driver, timeout: float = 8.0):
    """Wait until WhatsApp's reactions drawer is visible."""
    deadline = time.time() + timeout
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for sel in (S["rxn_dialog"], '[data-testid="drawer-right"]'):
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if not el.is_displayed():
                        continue
                    dialog = find(el, S["rxn_dialog"]) or el
                    if dialog.is_displayed():
                        return dialog
                except StaleElementReferenceException as exc:
                    last_err = exc
        time.sleep(0.15)
    raise TimeoutException("Reaction dialog did not appear") from last_err


def _message_id(row_el) -> str:
    """Return the WhatsApp message id from a row, its descendants, or ancestors."""
    mid = row_el.get_attribute("data-id") or ""
    if mid:
        return mid
    for sel in ("div[data-id]", '[data-id]'):
        inner = find(row_el, sel)
        if inner:
            mid = inner.get_attribute("data-id") or ""
            if mid:
                return mid
    try:
        cur = row_el
        for _ in range(10):
            parent = cur.find_element(By.XPATH, "..")
            mid = parent.get_attribute("data-id") or ""
            if mid:
                return mid
            cur = parent
    except (NoSuchElementException, StaleElementReferenceException):
        pass
    return ""


def _reaction_scope(row_el):
    """Return the row wrapper that also contains reaction bubbles."""
    try:
        return row_el.find_element(
            By.XPATH,
            "./ancestor::div[contains(@class,'focusable-list-item')][1]",
        )
    except (NoSuchElementException, StaleElementReferenceException):
        return row_el


def _is_outgoing(row_el) -> bool:
    """Detect whether the message was sent by the logged-in user."""
    mid = _message_id(row_el)
    if mid.startswith("true_"):
        return True
    if find(row_el, S["tail_out"]):
        return True
    try:
        cls = row_el.get_attribute("class") or ""
        if "message-out" in cls.split():
            return True
    except StaleElementReferenceException:
        pass
    return bool(find(row_el, ".message-out"))


def _parse_dom(driver, row_el) -> Optional[dict]:
    """Extract sender, body, quote, time, direction from separate DOM zones."""
    try:
        return driver.execute_script(_JS_PARSE_MESSAGE, row_el)
    except Exception:
        return None


def _extract_text(driver, row_el) -> str:
    """Return message body text from the body zone only (never combined row text)."""
    dom = _parse_dom(driver, row_el)
    if dom and dom.get("text"):
        return str(dom["text"]).strip()
    try:
        result = driver.execute_script(_JS_MSG_TEXT, row_el)
        if result:
            return str(result).strip()
    except Exception:
        pass

    container = find(row_el, '[data-testid="msg-container"]') or row_el
    quote_block = _find_quote_block(row_el)
    parts = []
    for sp in find_all(container, S["msg_text"]):
        try:
            if quote_block:
                inside = driver.execute_script(
                    "return arguments[0] && arguments[0].contains(arguments[1]);",
                    quote_block, sp,
                )
                if inside:
                    continue
        except Exception:
            pass
        t = sp.text.strip()
        if t:
            parts.append(t)
    return "\n".join(parts).strip()


def _extract_sender(driver, row_el, is_out: bool) -> str:
    """Resolve sender from the author div only."""
    if is_out:
        return "You"
    dom = _parse_dom(driver, row_el)
    if dom and dom.get("sender"):
        return str(dom["sender"]).strip()
    try:
        result = driver.execute_script(_JS_SENDER, row_el)
        if result and result != "You":
            return str(result).strip()
    except Exception:
        pass

    container = find(row_el, '[data-testid="msg-container"]') or row_el
    author_el = find(container, S["author"])
    if author_el:
        a = author_el.text.strip()
        if a:
            return a

    copyable = find(row_el, "[data-pre-plain-text]")
    if copyable:
        pre = copyable.get_attribute("data-pre-plain-text") or ""
        m = re.match(r"^\[[^\]]+\]\s*(.+?):\s*$", pre.strip())
        if m:
            return m.group(1).strip()

    return ""


def _find_quote_block(row_el):
    """Locate the quoted-message preview block via data-testid only."""
    container = find(row_el, '[data-testid="msg-container"]') or row_el
    for sel in (S["quote_block"],):
        block = find(container, sel)
        if block is not None:
            return block
    return None


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
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")

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
    log("Loading WhatsApp Web …")

    # Check for QR screen
    try:
        wait_for_any_selector(driver, QR_CODE_SELECTORS, timeout=25)
        log("┌─────────────────────────────────────────────┐")
        log("│  Scan the QR code in the browser with your  │")
        log("│  phone to log in to WhatsApp Web.           │")
        log("└─────────────────────────────────────────────┘")
    except TimeoutException:
        log("No QR screen detected – checking for an existing session …")

    wait_for_any_selector(driver, APP_READY_SELECTORS, timeout=timeout)
    log("Session active ✓")
    jitter(1.5, 2.5)


# ══════════════════════════════════════════════════════════════════════════════
#  Chat navigation
# ══════════════════════════════════════════════════════════════════════════════

_JS_SEARCH_RESULT_CELLS = r"""
var roots = arguments[0];
var root = null;
for (var r = 0; r < roots.length; r++) {
    root = document.querySelector(roots[r]);
    if (root) break;
}
if (!root) return [];

// WhatsApp keyboard focus walks [role="option"] rows in visual order.
var options = root.querySelectorAll('[role="option"]');
if (options.length) return Array.from(options);

// Fallback: outermost chat rows only (skip nested cell-frame-container copies).
var containers = root.querySelectorAll('[data-testid="cell-frame-container"]');
var outer = [];
for (var i = 0; i < containers.length; i++) {
    var c = containers[i];
    var nested = false;
    var par = c.parentElement;
    while (par && par !== root) {
        if (par.getAttribute && par.getAttribute('data-testid') === 'cell-frame-container') {
            nested = true;
            break;
        }
        par = par.parentElement;
    }
    if (!nested) outer.push(c);
}
return outer;
"""

_JS_CELL_CHAT_TITLE = r"""
var cell = arguments[0];
function looksLikeUnread(s) {
    s = (s || '').trim();
    if (!s) return true;
    if (/^\d+$/.test(s)) return true;
    if (/unread/i.test(s)) return true;
    return false;
}
function pickTitle(text, attr) {
    attr = (attr || '').trim();
    text = (text || '').trim();
    if (attr && !looksLikeUnread(attr)) return attr;
    if (text && !looksLikeUnread(text)) return text;
    return '';
}

var frame = cell.querySelector('[data-testid="cell-frame-title"]');
if (frame) {
    var titled = frame.querySelector('span[title]');
    if (titled) {
        var fromTitled = pickTitle(titled.textContent, titled.getAttribute('title'));
        if (fromTitled) return fromTitled;
    }
    var spans = frame.querySelectorAll('span');
    for (var i = 0; i < spans.length; i++) {
        var sp = spans[i];
        if (sp.closest('[data-icon], .wds-icon, [aria-label*="unread"]')) continue;
        var picked = pickTitle(sp.textContent, sp.getAttribute('title'));
        if (picked) return picked;
    }
}

var any = cell.querySelectorAll('span[title]');
for (var j = 0; j < any.length; j++) {
    var el = any[j];
    if (el.closest('[data-icon], .wds-icon, [data-testid="icon-unread-count"], [aria-label*="unread"]')) continue;
    var a = pickTitle(el.textContent, el.getAttribute('title'));
    if (a) return a;
}
return '';
"""

_JS_READ_HEADER_TITLE = r"""
var header = document.querySelector('#main header [data-testid="conversation-info-header"]')
    || document.querySelector('#main header');
if (!header) return '';

function badTitle(s) {
    s = (s || '').trim();
    if (!s) return true;
    if (/click here for group info/i.test(s)) return true;
    if (/^\+\d/.test(s)) return true;
    if (/\d+\s+more$/i.test(s)) return true;
    if ((s.match(/,/g) || []).length >= 3) return true;
    if (s.length > 100) return true;
    return false;
}

var zones = [
    '[data-testid="conversation-info-header-chat-title"]',
    '[data-testid="cell-frame-title"]'
];
for (var z = 0; z < zones.length; z++) {
    var box = header.querySelector(zones[z]);
    if (!box) continue;
    var sp = box.querySelector('span[title]') || box.querySelector('span');
    if (!sp) continue;
    var t = (sp.getAttribute('title') || sp.textContent || '').trim();
    if (!badTitle(t)) return t;
}

var best = '';
var spans = header.querySelectorAll('span[title]');
for (var i = 0; i < spans.length; i++) {
    var el = spans[i];
    if (el.closest('.wds-icon, [data-icon]')) continue;
    var t = (el.getAttribute('title') || '').trim();
    if (badTitle(t)) continue;
    if (!best || t.length < best.length) best = t;
}
return best;
"""

_HEADER_GARBAGE_RE = re.compile(
    r"click here for group info|\+\d{1,3}[\s\-]?\d|\d+\s+more$",
    re.IGNORECASE,
)


def _norm_chat_name(value: str) -> str:
    """Normalise a chat title for comparison (case + whitespace)."""
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _cell_chat_title(driver, cell) -> str:
    """Read the chat title from a search-result row (prefers span[title])."""
    try:
        title = driver.execute_script(_JS_CELL_CHAT_TITLE, cell)
        if title:
            return str(title).strip()
    except Exception:
        pass

    for sel in ('span[title]', S["cell_title"]):
        for el in cell.find_elements(By.CSS_SELECTOR, sel):
            attr = (el.get_attribute("title") or "").strip()
            if attr:
                return attr
            text = (el.text or "").strip()
            if text:
                return text
    return ""


def _find_search_result_cells(driver) -> List[object]:
    """Return top-level search rows in WhatsApp visual / keyboard order."""
    try:
        cells = driver.execute_script(
            _JS_SEARCH_RESULT_CELLS,
            SEARCH_RESULTS_ROOT_SELECTORS,
        )
        if cells:
            return list(cells)
    except Exception:
        pass

    root = None
    for sel in SEARCH_RESULTS_ROOT_SELECTORS:
        found = driver.find_elements(By.CSS_SELECTOR, sel)
        if found:
            root = found[0]
            break

    if root is None:
        return []

    cells = root.find_elements(By.CSS_SELECTOR, '[role="option"]')
    if cells:
        return cells

    return root.find_elements(By.CSS_SELECTOR, S["chat_cell"])


def _looks_like_header_garbage(title: str) -> bool:
    """True for participant lists, phone numbers, and other non-title header text."""
    t = (title or "").strip()
    if not t or t.startswith("wds-"):
        return True
    if _HEADER_GARBAGE_RE.search(t):
        return True
    if t.count(",") >= 3 or len(t) > 100:
        return True
    return False


def _read_open_chat_title(driver) -> str:
    """Return the chat/group name from the conversation header."""
    try:
        title = driver.execute_script(_JS_READ_HEADER_TITLE)
        if title and not _looks_like_header_garbage(str(title)):
            return str(title).strip()
    except Exception:
        pass

    for sel in CONV_HEADER_TITLE_SELECTORS:
        for el in driver.find_elements(By.CSS_SELECTOR, sel):
            title = (el.get_attribute("title") or el.text or "").strip()
            if title and not _looks_like_header_garbage(title):
                return title
    return ""


def _compose_box_visible(driver) -> bool:
    """True when the message compose box is visible (chat is really open)."""
    for sel in COMPOSE_BOX_SELECTORS:
        for el in driver.find_elements(By.CSS_SELECTOR, sel):
            try:
                if el.is_displayed():
                    return True
            except StaleElementReferenceException:
                continue
    return False


def _wait_for_chat_open(driver, name: str, timeout: float = 20) -> bool:
    """Block until the conversation footer appears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _compose_box_visible(driver):
            opened = _read_open_chat_title(driver)
            if opened and not _chat_title_matches(name, opened):
                log(f"  ⚠  Header shows {opened!r}, wanted {name!r}")
                return False
            return True

        opened = _read_open_chat_title(driver)
        if opened and _chat_title_matches(name, opened):
            jitter(0.4, 0.8)
            if _compose_box_visible(driver):
                return True

        time.sleep(0.25)
    return False


def _open_first_exact_search_result(driver, name: str) -> bool:
    """Click the first search row whose title exactly matches `name`."""
    needle = _norm_chat_name(name)
    for idx, cell in enumerate(_find_search_result_cells(driver)):
        title = _cell_chat_title(driver, cell)
        if _norm_chat_name(title) != needle:
            continue

        log(f"Opening chat: {title!r}")
        _click_search_result(driver, cell, title)
        if _wait_for_chat_open(driver, name):
            log("  Chat open ✓")
            return True

        log(f"  ⚠  Chat did not open for {name!r}")
        return False

    return False


def _chat_title_matches(wanted: str, opened: str) -> bool:
    """Return True when the opened header matches the requested chat name."""
    w = _norm_chat_name(wanted)
    o = _norm_chat_name(opened)
    if not w or not o:
        return False
    return w == o or w in o or o in w


def _click_search_result(driver, cell, title: str) -> None:
    """Click the titled span inside a search row (most reliable target)."""
    target = cell
    try:
        row = driver.execute_script(
            'return arguments[0].closest(\'[role="option"]\') || arguments[0];',
            cell,
        )
        if row:
            target = row
    except Exception:
        pass

    try:
        titled = driver.execute_script(
            """
            var root = arguments[0];
            var needle = arguments[1].toLowerCase().trim();
            var spans = root.querySelectorAll(
                '[data-testid="cell-frame-title"] span, span[title]'
            );
            for (var i = 0; i < spans.length; i++) {
                var sp = spans[i];
                var t = (sp.getAttribute('title') || sp.textContent || '')
                    .replace(/\\s+/g, ' ').trim().toLowerCase();
                if (t === needle) return sp;
            }
            return null;
            """,
            target,
            title,
        )
        if titled:
            target = titled
    except Exception:
        pass

    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center'});", target)
    jitter(0.25, 0.45)

    clicked = False
    for attempt in (
        lambda: target.click(),
        lambda: ActionChains(driver).move_to_element(target).click().perform(),
        lambda: driver.execute_script("arguments[0].click();", target),
    ):
        try:
            attempt()
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        driver.execute_script(
            """
            var el = arguments[0];
            ['mousedown', 'mouseup', 'click'].forEach(function(type) {
                el.dispatchEvent(new MouseEvent(type, {
                    bubbles: true, cancelable: true, view: window
                }));
            });
            """,
            target,
        )
    jitter(0.8, 1.4)


def open_chat(driver: uc.Chrome, name: str) -> bool:
    """
    Type the group name into the search box and open the first exact match.
    """
    wait_for_any_selector(driver, APP_READY_SELECTORS, timeout=30)
    box = find_search_input(driver)
    if box is None:
        log("  ✗  Could not find the WhatsApp search input.")
        return False

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'}); arguments[0].focus();",
            box,
        )
        box.click()
    except ElementNotInteractableException:
        driver.execute_script("arguments[0].focus();", box)
    jitter(0.4, 0.8)

    try:
        box.clear()
    except Exception:
        box.send_keys(Keys.CONTROL, "a")
        box.send_keys(Keys.DELETE)

    log(f"Searching for {name!r} …")
    for ch in name:
        box.send_keys(ch)
        time.sleep(random.uniform(0.04, 0.16))
    jitter(1.0, 1.8)

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, S["chat_cell"]))
        )
    except TimeoutException:
        log(f"  ✗  No chat found for {name!r}.")
        return False

    if _open_first_exact_search_result(driver, name):
        return True

    log(f"  ✗  Could not open {name!r} from search results.")
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  Parsing helpers
# ══════════════════════════════════════════════════════════════════════════════


def _parse_quote(driver, row_el) -> Optional[Quote]:
    """Extract quote sender + text from the quoted-message div only."""
    dom = _parse_dom(driver, row_el)
    if dom and dom.get("quote"):
        raw = dom["quote"]
        return Quote(
            sender=(raw.get("sender") or "").strip(),
            text=(raw.get("text") or "").strip(),
        )

    try:
        raw = driver.execute_script(_JS_QUOTE_PARSE, row_el)
        if raw:
            return Quote(
                sender=(raw.get("sender") or "").strip(),
                text=(raw.get("text") or "").strip(),
            )
    except Exception:
        pass

    block = _find_quote_block(row_el)
    if block is None:
        return None

    author_el = find(block, '[data-testid="quoted-author"]')
    if author_el is None:
        for sp in find_all(block, '[testid="author"], [data-testid="author"]'):
            author_el = sp
            break
    text_el = find(block, S["quote_text"])
    q_sender  = author_el.text.strip() if author_el else ""
    q_text    = text_el.text.strip() if text_el else ""

    if not q_text:
        for sp in find_all(block, 'span[dir], div[dir]'):
            t = sp.text.strip()
            if t and t != q_sender and not re.match(r"^\d{1,2}:\d{2}", t):
                q_text = t
                break

    if not q_sender and not q_text:
        return None
    return Quote(sender=q_sender, text=q_text)


def _parse_reactions_basic(driver, row_el) -> List[Reaction]:
    """
    Collect emoji reactions from the inline pills without any clicks.
    Returns a list of Reaction(emoji, count, senders=[]).
    """
    try:
        raw = driver.execute_script(_JS_REACTIONS, row_el) or []
        return [
            Reaction(emoji=item["emoji"], count=int(item.get("count", 1)), senders=[])
            for item in raw
            if item.get("emoji")
        ]
    except Exception:
        pass

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


def _collect_reaction_senders(driver, dialog) -> List[str]:
    """Scroll the reactions popup and collect every participant name."""
    try:
        names = driver.execute_script(_JS_COLLECT_REACTORS, dialog) or []
        cleaned = [str(n).strip() for n in names if str(n).strip()]
        if cleaned:
            return cleaned
    except Exception:
        pass

    senders: List[str] = []
    seen: set = set()
    scroll_el = (
        find(dialog, S["rxn_scroll"])
        or find(dialog, '[role="list"]')
        or dialog
    )

    for _ in range(40):
        for row in find_all(dialog, S["rxn_row"]):
            try:
                role = (row.get_attribute("role") or "").lower()
                testid = row.get_attribute("data-testid") or ""
                if role == "tab" or testid == "reactions-tab":
                    continue
            except StaleElementReferenceException:
                continue

            for name_el in find_all(row, S["rxn_name"]):
                n = (name_el.get_attribute("title") or name_el.text or "").strip()
                if n and n not in seen:
                    seen.add(n)
                    senders.append(n)

            if not find_all(row, S["rxn_name"]):
                for sp in find_all(row, 'span[title], span[dir="auto"]'):
                    n = (sp.get_attribute("title") or sp.text or "").strip()
                    if n and n not in seen and not re.fullmatch(r"\d+", n):
                        seen.add(n)
                        senders.append(n)

        try:
            prev = driver.execute_script("return arguments[0].scrollTop;", scroll_el)
            driver.execute_script("arguments[0].scrollTop += 180;", scroll_el)
            cur = driver.execute_script("return arguments[0].scrollTop;", scroll_el)
            if cur == prev:
                break
        except Exception:
            break
        jitter(0.15, 0.35)

    return senders


def _find_reaction_pill(row_el, emoji: str):
    """Locate the clickable reaction pill for a given emoji."""
    scopes = [row_el]
    try:
        parent = row_el.find_element(By.XPATH, "..")
        scopes.append(parent)
    except (NoSuchElementException, StaleElementReferenceException):
        pass

    try:
        root = row_el.find_element(By.XPATH, "./ancestor::div[contains(@class,'focusable-list-item')]")
        scopes.append(root)
    except (NoSuchElementException, StaleElementReferenceException):
        pass

    seen = set()
    for scope in scopes:
        if id(scope) in seen:
            continue
        seen.add(id(scope))
        for pill in find_all(scope, S["rxn_pill"]):
            try:
                txt  = pill.text.strip()
                aria = (pill.get_attribute("aria-label") or "").strip()
                if _emoji_matches(emoji, txt) or _emoji_matches(emoji, aria):
                    return pill
            except StaleElementReferenceException:
                continue

        for container in find_all(scope, '[data-testid="addon-bubble-container"]'):
            for pill in find_all(container, '[data-testid="reaction-bubble"]'):
                try:
                    aria = (pill.get_attribute("aria-label") or "").strip()
                    if _emoji_matches(emoji, pill.text.strip()) or _emoji_matches(emoji, aria):
                        return pill
                except StaleElementReferenceException:
                    continue

        for container in find_all(scope, S["rxn_container"]):
            for pill in find_all(container, '[role="button"], button'):
                try:
                    if _emoji_matches(emoji, pill.text.strip()):
                        return pill
                except StaleElementReferenceException:
                    continue
    return None


def _dismiss_reaction_dialog(driver) -> None:
    """Close an open reactions popup without disturbing chat scroll."""
    try:
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        jitter(0.15, 0.3)
    except Exception:
        pass


def _parse_reactions_detail(driver, row_el) -> List[Reaction]:
    """
    Click each reaction pill to open the 'who reacted' dialog, scroll the
    participant list, then harvest names.  Much slower – use only with
    --reactions-detail.

    Uses JS click (no scrollIntoView) so the message panel position is kept.
    """
    basic    = _parse_reactions_basic(driver, row_el)
    detailed = []

    for rxn in basic:
        senders: List[str] = []
        target_pill = _find_reaction_pill(row_el, rxn.emoji)

        if target_pill:
            try:
                driver.execute_script("arguments[0].click();", target_pill)
                jitter(0.45, 0.9)

                dialog = _wait_for_reaction_dialog(driver)

                tabs = find_all(dialog, S["rxn_tab"])
                if len(tabs) > 1:
                    for tab in tabs:
                        tab_text = tab.text.strip()
                        if _emoji_matches(rxn.emoji, tab_text):
                            driver.execute_script("arguments[0].click();", tab)
                            jitter(0.25, 0.45)
                            break

                jitter(0.2, 0.4)
                senders = _collect_reaction_senders(driver, dialog)
                _dismiss_reaction_dialog(driver)

            except (TimeoutException, StaleElementReferenceException,
                    ElementNotInteractableException):
                _dismiss_reaction_dialog(driver)

        detailed.append(Reaction(emoji=rxn.emoji, count=rxn.count, senders=senders))

    return detailed


def _parse_message(
    driver,
    row_el,
    current_date: str,
    reactions_detail: bool = False,
) -> Optional[Message]:
    """
    Parse a single message row into a Message dataclass.
    Each field is read from its own DOM zone — never from combined row text.
    Returns None for system-event rows (e.g. "You were added to the group").
    """
    try:
        mid = _message_id(row_el)
        if not mid:
            return None

        dom = _parse_dom(driver, row_el)

        is_out = (
            dom.get("isOutgoing")
            if dom and "isOutgoing" in dom
            else _is_outgoing(row_el)
        )

        quote = _parse_quote(driver, row_el)
        sender = (
            str(dom.get("sender") or "").strip()
            if dom and dom.get("sender")
            else _extract_sender(driver, row_el, is_out)
        )

        msg_time = ""
        if dom and dom.get("time"):
            msg_time = str(dom["time"]).strip()
        if not msg_time:
            meta_el = find(row_el, S["msg_meta"])
            if meta_el:
                raw = meta_el.text.strip()
                m   = re.search(r"\d{1,2}:\d{2}(?:\s?[AaPp][Mm])?", raw)
                msg_time = m.group(0) if m else raw.split()[-1]

        text = (
            str(dom.get("text") or "").strip()
            if dom and dom.get("text")
            else _extract_text(driver, row_el)
        )

        media_type = None
        if not text:
            for mtype, sel in MEDIA_CHECKS:
                if find(row_el, sel):
                    media_type = mtype
                    break

        if not sender and not text and media_type is None:
            return None

        scope = _reaction_scope(row_el)
        if reactions_detail:
            reactions = _parse_reactions_detail(driver, scope)
        else:
            reactions = _parse_reactions_basic(driver, scope)

        return Message(
            id          = mid,
            date        = current_date,
            time        = msg_time,
            sender      = sender,
            text        = text,
            is_outgoing = is_out,
            quote       = quote,
            reactions   = reactions,
            media_type  = media_type,
        )

    except StaleElementReferenceException:
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  Main scroll + harvest loop
# ══════════════════════════════════════════════════════════════════════════════


def _panel_items(panel):
    """Return date cards and message rows currently in the DOM."""
    try:
        return panel.find_elements(By.CSS_SELECTOR, CHAT_ITEM_SELECTOR)
    except StaleElementReferenceException:
        return []


def _harvest_visible_messages(
    driver,
    panel,
    messages: Dict[str, Message],
    current_date: str,
    reactions_detail: bool,
) -> tuple[int, str]:
    """Parse visible rows; return (new_count, updated_current_date)."""
    added = 0

    for item in _panel_items(panel):
        try:
            testid = item.get_attribute("data-testid") or ""
            if testid == "msg-dateseparator":
                raw = item.text.strip()
                if raw:
                    current_date = normalize_date(raw)
                continue

            if testid == "msg-container":
                row_el = item
            elif item.get_attribute("data-id"):
                row_el = find(item, S["message_row"]) or item
            else:
                continue

            msg = _parse_message(
                driver, row_el, current_date, reactions_detail=reactions_detail
            )
            if msg and msg.id not in messages:
                messages[msg.id] = msg
                added += 1

        except StaleElementReferenceException:
            continue

    return added, current_date


def _scroll_to_top(
    driver,
    panel,
    scroll_pause: float,
    max_steps: int,
) -> None:
    """Single upward scroll until the oldest loaded history is reached."""
    prev_top = -1
    stall_n  = 0

    for step in range(1, max_steps + 1):
        wait_no_spinner(driver, panel)
        top = driver.execute_script("return arguments[0].scrollTop;", panel)

        if step == 1 or step % 20 == 0:
            log(f"  ↑ scrollTop={top}")

        if top == 0:
            jitter(0.8, 1.5)
            wait_no_spinner(driver, panel)
            if driver.execute_script("return arguments[0].scrollTop;", panel) == 0:
                log(f"  ↑ reached top ({step} steps)")
                return

        if top == prev_top:
            stall_n += 1
            jitter(1.5, 2.5)
            wait_no_spinner(driver, panel)
            if stall_n >= 3:
                log(f"  ↑ top of loaded history (scrollTop={top})")
                return
        else:
            stall_n = 0

        prev_top = top
        scroll_by(driver, panel, -random.randint(450, 750))
        jitter(scroll_pause * 0.25, scroll_pause * 0.6)


def scrape_chat(
    driver:           uc.Chrome,
    scroll_pause:     float = 1.0,
    max_top_scrolls:  int   = 6000,
    max_down_passes:  int   = 10,
    reactions_detail: bool  = False,
) -> List[Message]:
    """
    1. Wait for the message panel.
    2. One upward scroll to the start of history.
    3. One downward scroll, harvesting messages (and reactors) as they appear.
    4. Stop when the bottom is reached with no new messages.
    """

    # ── wait for panel ────────────────────────────────────────────────────────
    panel = WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, S["panel"]))
    )

    # ── single upward scroll ──────────────────────────────────────────────────
    log("↑  Rewinding to the start of history …")
    _scroll_to_top(driver, panel, scroll_pause, max_top_scrolls)

    if reactions_detail:
        log("↓  Reading messages and collecting reactors …")
    else:
        log("↓  Reading messages …")
    jitter(0.8, 1.4)

    # ── single downward scroll + harvest ────────────────────────────────────────
    messages:           Dict[str, Message] = {}
    current_date:       str = datetime.today().strftime("%d/%m/%Y")
    no_new_streak       = 0
    step_n              = 0
    last_scroll_top     = -1
    same_scroll_streak  = 0
    max_scroll_top      = 0

    while step_n < max_down_passes:
        step_n += 1
        wait_no_spinner(driver, panel)

        try:
            added, current_date = _harvest_visible_messages(
                driver,
                panel,
                messages,
                current_date,
                reactions_detail,
            )
        except StaleElementReferenceException:
            panel = driver.find_element(By.CSS_SELECTOR, S["panel"])
            added, current_date = _harvest_visible_messages(
                driver,
                panel,
                messages,
                current_date,
                reactions_detail,
            )

        no_new_streak = 0 if added else no_new_streak + 1

        scroll_top    = driver.execute_script("return arguments[0].scrollTop;", panel)
        scroll_height = driver.execute_script("return arguments[0].scrollHeight;", panel)
        client_height = driver.execute_script("return arguments[0].clientHeight;", panel)
        max_scroll_top = max(max_scroll_top, scroll_top)
        at_bottom      = (scroll_top + client_height) >= (scroll_height - 20)

        if scroll_top == last_scroll_top:
            same_scroll_streak += 1
        else:
            same_scroll_streak = 0
        last_scroll_top = scroll_top

        if step_n == 1 or step_n % 15 == 0 or added:
            log(
                f"  ↓ step {step_n}: {len(messages)} total"
                f" (+{added} new), scrollTop={scroll_top}"
            )

        if at_bottom and no_new_streak >= 8:
            log(f"\n✓  End of chat reached.  {len(messages)} messages captured.")
            break

        if same_scroll_streak >= 10 and no_new_streak >= 6:
            log(
                f"\n✓  Scroll stalled near end.  {len(messages)} messages captured."
            )
            break

        if (
            no_new_streak >= 15
            and scroll_top >= max_scroll_top - 40
            and step_n > 8
        ):
            log(
                f"\n✓  No new messages near bottom.  "
                f"{len(messages)} messages captured."
            )
            break

        scroll_by(driver, panel, random.randint(90, 160))
        jitter(scroll_pause * 0.35, scroll_pause * 0.75)

    else:
        log(
            f"\n⚠  Stopped after {max_down_passes} down-scroll steps.  "
            f"{len(messages)} messages captured."
        )

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
        help="Max upward scroll attempts when rewinding to top (default: 6000).",
    )
    ap.add_argument(
        "--max-down-passes", type=int, default=2500,
        help="Max downward scroll passes when reading messages (default: 2500).",
    )
    ap.add_argument(
        "--reactions-detail", action="store_true",
        help="Click each reaction pill to record who reacted (slower).",
    )
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass

    driver = build_driver(args.profile_dir)
    try:
        driver.get("https://web.whatsapp.com/")
        wait_for_login(driver)

        if not open_chat(driver, args.group):
            log("Could not open the group.  Exiting.")
            sys.exit(1)

        messages = scrape_chat(
            driver,
            scroll_pause     = args.scroll_pause,
            max_top_scrolls  = args.max_top_scrolls,
            max_down_passes  = args.max_down_passes,
            reactions_detail = args.reactions_detail,
        )

        output_path = Path(args.output)
        payload     = [asdict(m) for m in messages]
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(f"✓  Saved {len(messages)} messages → {output_path}")

    finally:
        input("\nPress Enter to close the browser …")
        driver.quit()


if __name__ == "__main__":
    main()
