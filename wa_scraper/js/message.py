"""JavaScript snippets for message DOM extraction."""

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

