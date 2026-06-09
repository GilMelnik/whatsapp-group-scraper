"""JavaScript snippets for chat search and navigation."""

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

