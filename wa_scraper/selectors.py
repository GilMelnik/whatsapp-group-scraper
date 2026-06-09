"""CSS selectors for WhatsApp Web DOM elements."""
from __future__ import annotations

from typing import Dict, List

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
    "rxn_dialog":    (
        '[data-testid="reactions-list"],'
        '[role="dialog"],'
        '[role="list"]'
    ),
    "rxn_tab":       '[data-testid="reactions-tab"]',
    "rxn_name":      (
        '[data-testid="react-participant"],'
        '[data-testid="reactions-list-item"],'
        '[data-testid="reactions-details-cell-not-me"],'
        '[data-testid="reactions-details-cell-me"],'
        '[data-testid="cell-frame-title"]'
    ),
    "rxn_row":       (
        '[data-testid="react-participant"],'
        '[data-testid="reactions-list-item"],'
        '[data-testid="reactions-details-cell-not-me"],'
        '[data-testid="reactions-details-cell-me"],'
        '[role="listitem"][aria-label*="reacted"],'
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

