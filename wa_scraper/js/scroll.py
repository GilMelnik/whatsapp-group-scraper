"""JavaScript snippets for chat panel scrolling."""

_JS_SCROLL_HELPERS = r"""
function _waFindScrollEl(panel) {
    function score(el) {
        if (!el || el.scrollHeight <= el.clientHeight + 2) return -1;
        var oy = window.getComputedStyle(el).overflowY;
        if (oy !== 'auto' && oy !== 'scroll' && oy !== 'overlay') return -1;
        var rows = el.querySelectorAll(
            'div.focusable-list-item[data-id], [data-testid="msg-container"]'
        ).length;
        return rows * 10000 + el.scrollHeight;
    }
    var best = panel;
    var bestScore = score(panel);
    var pref = panel.querySelector('[tabindex="-1"]');
    if (pref) {
        var ps = score(pref);
        if (ps > bestScore) { bestScore = ps; best = pref; }
    }
    var nodes = panel.querySelectorAll(
        '[role="application"], [data-scrolltraceid], div'
    );
    for (var i = 0; i < nodes.length; i++) {
        var s = score(nodes[i]);
        if (s > bestScore) { bestScore = s; best = nodes[i]; }
    }
    var p = panel.parentElement;
    while (p && p !== document.body) {
        var s = score(p);
        if (s > bestScore) { bestScore = s; best = p; }
        p = p.parentElement;
    }
    return best;
}
function _waTopmostRow(panel) {
    var rows = panel.querySelectorAll('div.focusable-list-item[data-id]');
    var topRow = null;
    var minY = Infinity;
    for (var i = 0; i < rows.length; i++) {
        var rect = rows[i].getBoundingClientRect();
        if (rect.top < minY) {
            minY = rect.top;
            topRow = rows[i];
        }
    }
    return topRow;
}
function _waDispatchWheel(scrollEl, deltaY) {
    try {
        scrollEl.dispatchEvent(new WheelEvent('wheel', {
            deltaY: deltaY,
            deltaMode: 0,
            bubbles: true,
            cancelable: true,
            view: window
        }));
    } catch (e) {}
}
function _waIsReversed(el) {
    function hasReverse(node) {
        try {
            var fd = window.getComputedStyle(node).flexDirection || '';
            if (fd.indexOf('reverse') >= 0) return true;
        } catch (e) {}
        return false;
    }
    if (hasReverse(el)) return true;
    var kids = el.querySelectorAll(
        '[role="row"], [data-testid="msg-container"], div.focusable-list-item'
    );
    for (var i = 0; i < kids.length && i < 16; i++) {
        if (hasReverse(kids[i])) return true;
    }
    // Fresh chats open at the newest messages with raw scrollTop ~= 0.
    if (el.scrollTop >= -2 && el.scrollTop <= 2) {
        var rows = el.querySelectorAll('div.focusable-list-item[data-id]');
        if (rows.length > 0) {
            var last = rows[rows.length - 1];
            var rect = last.getBoundingClientRect();
            var er = el.getBoundingClientRect();
            if (rect.bottom >= er.bottom - 40) return true;
        }
    }
    return false;
}
function _waLogicalTop(el, reversed) {
    if (reversed) {
        return el.scrollTop + el.scrollHeight - el.clientHeight;
    }
    return el.scrollTop;
}
function _waSetLogicalTop(el, reversed, logical) {
    if (reversed) {
        el.scrollTop = logical - el.scrollHeight + el.clientHeight;
    } else {
        el.scrollTop = logical;
    }
}
function _waMaxLogical(el) {
    return Math.max(0, el.scrollHeight - el.clientHeight);
}
function _waAtTop(el, reversed) {
    return _waLogicalTop(el, reversed) <= 2;
}
function _waAtBottom(el, reversed) {
    return _waLogicalTop(el, reversed) >= _waMaxLogical(el) - 8;
}
"""

_JS_PANEL_METRICS = (
    _JS_SCROLL_HELPERS
    + r"""
return (function(panel) {
    var scrollEl = _waFindScrollEl(panel);
    var reversed = _waIsReversed(scrollEl);
    var panelRect = panel.getBoundingClientRect();
    var rows = panel.querySelectorAll('div.focusable-list-item[data-id], [data-testid="msg-container"]');
    var visible = [];
    var maxBottom = -Infinity;
    var minTop = Infinity;
    var anchorBottom = null;
    var oldestId = '';
    var oldestTop = Infinity;

    for (var i = 0; i < rows.length; i++) {
        var el = rows[i];
        var rect = el.getBoundingClientRect();
        if (rect.bottom <= panelRect.top + 1) continue;
        if (rect.top >= panelRect.bottom - 1) continue;

        var dataId = el.getAttribute('data-id') || '';
        if (!dataId) {
            var inner = el.querySelector('[data-id]');
            if (inner) dataId = inner.getAttribute('data-id') || '';
        }
        var relTop = rect.top - panelRect.top;
        var relBottom = rect.bottom - panelRect.top;
        visible.push({
            id: dataId,
            top: relTop,
            bottom: relBottom,
            height: rect.height
        });
        if (relBottom > maxBottom) {
            maxBottom = relBottom;
            anchorBottom = relBottom;
        }
        if (relTop < minTop) minTop = relTop;
        if (dataId && relTop < oldestTop) {
            oldestTop = relTop;
            oldestId = dataId;
        }
    }

    var logicalTop = _waLogicalTop(scrollEl, reversed);
    return {
        scrollTop: logicalTop,
        rawScrollTop: scrollEl.scrollTop,
        scrollHeight: scrollEl.scrollHeight,
        clientHeight: scrollEl.clientHeight,
        visibleCount: visible.length,
        anchorBottom: anchorBottom,
        anchorTop: minTop === Infinity ? null : minTop,
        oldestId: oldestId,
        reversed: reversed,
        atBottom: _waAtBottom(scrollEl, reversed),
        atTop: _waAtTop(scrollEl, reversed),
        visible: visible
    };
})(arguments[0]);
"""
)

_JS_PANEL_SCROLL = (
    _JS_SCROLL_HELPERS
    + r"""
return (function(panel, direction, overlap) {
    overlap = overlap || 28;
    direction = direction || 'down';
    var scrollEl = _waFindScrollEl(panel);
    var reversed = _waIsReversed(scrollEl);
    var panelRect = panel.getBoundingClientRect();
    var rows = panel.querySelectorAll('div.focusable-list-item[data-id], [data-testid="msg-container"]');
    var prevLogical = _waLogicalTop(scrollEl, reversed);
    var prevRaw = scrollEl.scrollTop;
    var delta = 0;

    if (direction === 'down') {
        var maxBottom = -Infinity;
        for (var i = 0; i < rows.length; i++) {
            var el = rows[i];
            var rect = el.getBoundingClientRect();
            if (rect.bottom <= panelRect.top + 1) continue;
            if (rect.top >= panelRect.bottom - 1) continue;
            var relBottom = rect.bottom - panelRect.top;
            if (relBottom > maxBottom) maxBottom = relBottom;
        }
        if (maxBottom > -Infinity) {
            delta = Math.max(12, maxBottom - overlap);
        } else {
            delta = Math.max(48, Math.floor(scrollEl.clientHeight * 0.45));
        }
        _waSetLogicalTop(scrollEl, reversed, prevLogical + delta);
    } else {
        delta = Math.max(350, Math.floor(scrollEl.clientHeight * 0.8));
        var topRow = _waTopmostRow(panel);
        if (topRow) {
            var sRect = scrollEl.getBoundingClientRect();
            var tRect = topRow.getBoundingClientRect();
            var offset = Math.max(0, tRect.top - sRect.top - 12);
            delta = Math.max(delta, offset + 180);
        }
        if (topRow) {
            try {
                topRow.scrollIntoView({block: 'start', inline: 'nearest'});
            } catch (e) {}
        }
        var targets = [scrollEl];
        if (panel !== scrollEl) targets.push(panel);
        for (var ti = 0; ti < targets.length; ti++) {
            targets[ti].scrollTop -= delta;
            _waDispatchWheel(targets[ti], -delta);
        }
    }

    var logicalTop = _waLogicalTop(scrollEl, reversed);
    return {
        scrolled: logicalTop - prevLogical,
        delta: delta,
        scrollTop: logicalTop,
        rawScrollTop: scrollEl.scrollTop,
        reversed: reversed,
        atBottom: _waAtBottom(scrollEl, reversed),
        atTop: _waAtTop(scrollEl, reversed),
        stuck: Math.abs(logicalTop - prevLogical) < 1
            && Math.abs(scrollEl.scrollTop - prevRaw) < 1
    };
})(arguments[0], arguments[1], arguments[2]);
"""
)

_JS_DATE_HELPERS = r"""
function _waCleanDateText(text) {
    return (text || '')
        .replace(/[\u200e\u200f\u202a-\u202e]/g, '')
        .replace(/\s+/g, ' ')
        .trim();
}
function _waIsSystemChatNotification(text) {
    var lc = (text || '').toLowerCase();
    var patterns = [
        /\+\d[\d\s\-()]{6,}\d/,
        /(?<!\d)\d[\d\s\-()]{7,}\d(?!\d)/,
        /\badded\b/, /\bwas added\b/, /\bwere added\b/,
        /\bjoined\b/, /\binvite link\b/,
        /\bremoved\b/, /\bwas removed\b/,
        /\bturned on\b/, /\bturned off\b/,
        /\bdisappearing messages\b/,
        /\badmin\b/, /\badmins\b/,
        /\brecalled\b/, /\bdeleted\b/,
        /\bleft\b/, /\bcreated\b/,
        /\bchanged\b/, /\bsecurity code\b/,
        /\bforwarded\b/
    ];
    for (var i = 0; i < patterns.length; i++) {
        if (patterns[i].test(text) || patterns[i].test(lc)) return true;
    }
    return false;
}
function _waLooksLikeNumericDate(text) {
    var m = text.match(/^(\d{1,2})[\/\.\-](\d{1,2})(?:[\/\.\-](\d{2,4}))?$/);
    if (!m) return false;
    var day = parseInt(m[1], 10);
    var mon = parseInt(m[2], 10);
    if (day < 1 || day > 31 || mon < 1 || mon > 12) return false;
    if (m[3]) {
        var year = parseInt(m[3], 10);
        if (m[3].length === 2) return year >= 0 && year <= 99;
        return year >= 2000 && year <= 2100;
    }
    return true;
}
function _waLooksLikeDateLabel(text) {
    text = _waCleanDateText(text);
    if (!text || text.length > 35) return false;
    if (/\n/.test(text)) return false;
    if (/^\d{1,2}:\d{2}/.test(text)) return false;
    if (_waIsSystemChatNotification(text)) return false;

    var lc = text.toLowerCase();
    if (lc === 'today' || lc === 'yesterday') return true;

    var enDays = [
        'monday', 'tuesday', 'wednesday', 'thursday',
        'friday', 'saturday', 'sunday'
    ];
    if (enDays.indexOf(lc) >= 0) return true;

    var heDays = [
        'היום', 'אתמול',
        'ראשון', 'שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת'
    ];
    if (heDays.indexOf(text) >= 0) return true;

    if (_waLooksLikeNumericDate(text)) return true;
    if (/^\d{1,2}\s+\S+\s+\d{4}$/.test(text)) return true;
    if (/^[A-Za-z\u0590-\u05FF]+\s+\d{1,2},?\s+\d{4}$/.test(text)) return true;

    return false;
}
function _waElPlainText(el) {
    if (!el) return '';
    return _waCleanDateText(el.innerText || el.textContent || '');
}
function _waDateLabelFromEl(el) {
    if (!el) return '';
    if (el.getAttribute('data-testid') === 'msg-dateseparator') {
        return _waElPlainText(el);
    }

    var best = '';
    var spans = el.querySelectorAll('span');
    for (var i = 0; i < spans.length; i++) {
        var spanText = _waElPlainText(spans[i]);
        if (!_waLooksLikeDateLabel(spanText)) continue;
        if (!best || spanText.length < best.length) best = spanText;
    }
    if (best) return best;

    var raw = _waElPlainText(el);
    if (_waLooksLikeDateLabel(raw)) return raw;
    return '';
}
function _waHasFixedAncestor(el, panel) {
    var node = el;
    while (node && node !== panel && node !== document.body) {
        var pos = window.getComputedStyle(node).position;
        if (pos === 'fixed') return true;
        node = node.parentElement;
    }
    return false;
}
function _waIsInsideMessage(el) {
    return !!el.closest(
        'div.focusable-list-item[data-id], [data-testid="msg-container"], [data-testid="msg-meta"]'
    );
}
function _waMessageRowFromEl(el) {
    if (!el || el.nodeType !== 1) return null;
    if (el.getAttribute('data-testid') === 'msg-container') return el;
    if (el.classList.contains('focusable-list-item') && el.getAttribute('data-id')) return el;

    var nested = el.querySelector('[data-testid="msg-container"]')
        || el.querySelector('div.focusable-list-item[data-id]');
    return nested || null;
}
function _waFindMessageList(panel) {
    var scrollEl = _waFindScrollEl(panel);
    var sample = panel.querySelector('div.focusable-list-item[data-id]');
    if (!sample) return scrollEl;

    var best = scrollEl;
    var bestScore = -1;
    var node = sample.parentElement;
    while (node && node !== panel) {
        var kids = node.children;
        if (kids.length >= 2) {
            var msgKids = 0;
            var dateKids = 0;
            for (var i = 0; i < kids.length; i++) {
                if (_waMessageRowFromEl(kids[i])) msgKids++;
                else if (_waDateLabelFromEl(kids[i])) dateKids++;
            }
            var score = msgKids * 20 + dateKids * 40 + kids.length;
            if (score > bestScore) {
                bestScore = score;
                best = node;
            }
        }
        node = node.parentElement;
    }
    return best;
}
function _waListChildren(listRoot) {
    var children = [];
    for (var i = 0; i < listRoot.children.length; i++) {
        children.push(listRoot.children[i]);
    }
    return children;
}
function _waVisibleInPanel(el, panelRect) {
    var rect = el.getBoundingClientRect();
    return rect.bottom > panelRect.top + 1 && rect.top < panelRect.bottom - 1;
}
function _waDateSeparatorCandidates(panel) {
    var candidates = [];
    var seenTops = [];

    function addCandidate(el, text) {
        text = _waCleanDateText(text);
        if (!text || !el || !_waLooksLikeDateLabel(text)) return;
        if (_waIsInsideMessage(el)) return;
        if (_waHasFixedAncestor(el, panel)) return;
        if (el.querySelector('div.focusable-list-item[data-id], [data-testid="msg-container"]')) {
            return;
        }

        var rect = el.getBoundingClientRect();
        if (rect.height > 160 || rect.width < 16) return;

        for (var s = 0; s < seenTops.length; s++) {
            if (Math.abs(seenTops[s] - rect.top) < 6) return;
        }
        seenTops.push(rect.top);
        candidates.push({ text: text, top: rect.top, el: el });
    }

    var legacy = panel.querySelectorAll('[data-testid="msg-dateseparator"]');
    for (var l = 0; l < legacy.length; l++) {
        addCandidate(legacy[l], _waDateLabelFromEl(legacy[l]));
    }

    var tabDivs = panel.querySelectorAll('div[tabindex="-1"]');
    for (var t = 0; t < tabDivs.length; t++) {
        var tab = tabDivs[t];
        if (tab.querySelector('div.focusable-list-item[data-id], [data-testid="msg-container"]')) {
            continue;
        }
        if (tab.querySelector('a[href]')) continue;
        addCandidate(tab, _waDateLabelFromEl(tab));
    }

    var listRoot = _waFindMessageList(panel);
    var children = _waListChildren(listRoot);
    for (var c = 0; c < children.length; c++) {
        var child = children[c];
        if (_waMessageRowFromEl(child)) continue;
        addCandidate(child, _waDateLabelFromEl(child));
    }

    candidates.sort(function(a, b) { return a.top - b.top; });
    return candidates;
}
function _waDateForRow(panel, rowEl) {
    var listItem = rowEl.closest('div.focusable-list-item[data-id]') || rowEl;
    var rowTop = listItem.getBoundingClientRect().top;
    var candidates = _waDateSeparatorCandidates(panel);
    var best = '';
    var bestTop = -Infinity;

    for (var i = 0; i < candidates.length; i++) {
        var cand = candidates[i];
        if (cand.top <= rowTop + 10 && cand.top > bestTop) {
            bestTop = cand.top;
            best = cand.text;
        }
    }
    return best;
}
function _waRowTop(row, panelRect) {
    var listItem = row.closest('div.focusable-list-item[data-id]') || row;
    return listItem.getBoundingClientRect().top;
}
function _waRowKey(row) {
    var listItem = row.closest('div.focusable-list-item[data-id]');
    if (listItem) {
        return listItem.getAttribute('data-id') || '';
    }
    return row.getAttribute('data-id') || '';
}
function _waVisibleMessageRows(panel) {
    var panelRect = panel.getBoundingClientRect();
    var rows = panel.querySelectorAll(
        'div.focusable-list-item[data-id], [data-testid="msg-container"]'
    );
    var seen = {};
    var items = [];

    for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        if (!_waVisibleInPanel(row, panelRect)) continue;

        var key = _waRowKey(row);
        if (!key || seen[key]) continue;
        seen[key] = true;

        var listItem = row.closest('div.focusable-list-item[data-id]');
        var el = listItem || row;
        items.push({
            kind: 'message',
            top: _waRowTop(row, panelRect),
            el: el
        });
    }
    return items;
}
function _waVisibleDateSeparators(panel) {
    var panelRect = panel.getBoundingClientRect();
    var all = _waDateSeparatorCandidates(panel);
    var items = [];

    for (var i = 0; i < all.length; i++) {
        if (!_waVisibleInPanel(all[i].el, panelRect)) continue;
        items.push({
            kind: 'date',
            text: all[i].text,
            top: all[i].top
        });
    }
    return items;
}
function _waFindDateAbove(panel) {
    var panelRect = panel.getBoundingClientRect();
    var candidates = _waDateSeparatorCandidates(panel);
    var firstMsgTop = Infinity;

    var rows = panel.querySelectorAll('div.focusable-list-item[data-id]');
    for (var i = 0; i < rows.length; i++) {
        if (!_waVisibleInPanel(rows[i], panelRect)) continue;
        var top = rows[i].getBoundingClientRect().top;
        if (top < firstMsgTop) firstMsgTop = top;
    }

    var best = '';
    var bestTop = -Infinity;
    for (var c = 0; c < candidates.length; c++) {
        if (firstMsgTop < Infinity && candidates[c].top > firstMsgTop + 10) continue;
        if (candidates[c].top > bestTop) {
            bestTop = candidates[c].top;
            best = candidates[c].text;
        }
    }
    return best;
}
"""

_JS_CHAT_ITEMS = (
    _JS_SCROLL_HELPERS
    + _JS_DATE_HELPERS
    + r"""
return (function(panel) {
    var items = _waVisibleDateSeparators(panel).concat(_waVisibleMessageRows(panel));
    items.sort(function(a, b) { return a.top - b.top; });
    return items;
})(arguments[0]);
"""
)

_JS_DISCOVER_DATE = (
    _JS_SCROLL_HELPERS
    + _JS_DATE_HELPERS
    + r"""
return (function(panel, maxAttempts) {
    maxAttempts = maxAttempts || 8;
    var scrollEl = _waFindScrollEl(panel);
    var reversed = _waIsReversed(scrollEl);

    for (var attempt = 0; attempt < maxAttempts; attempt++) {
        var text = _waFindDateAbove(panel);
        if (text) return text;

        if (_waAtTop(scrollEl, reversed)) break;

        var delta = 180;
        var targets = [scrollEl];
        if (panel !== scrollEl) targets.push(panel);
        for (var i = 0; i < targets.length; i++) {
            targets[i].scrollTop -= delta;
            _waDispatchWheel(targets[i], -delta);
        }
    }

    return _waFindDateAbove(panel);
})(arguments[0], arguments[1]);
"""
)

_JS_DATE_FOR_ROW = (
    _JS_SCROLL_HELPERS
    + _JS_DATE_HELPERS
    + r"""
return (function(panel, rowEl) {
    return _waDateForRow(panel, rowEl);
})(arguments[0], arguments[1]);
"""
)

