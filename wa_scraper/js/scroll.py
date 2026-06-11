"""JavaScript snippets for chat panel scrolling."""

_JS_SCROLL_HELPERS = r"""
function _waMsgRowSelector() {
    return (
        'div.focusable-list-item[data-id], ' +
        '[data-testid^="conv-msg-"][data-id], ' +
        'div.xscbp6u[data-id], ' +
        '[data-testid="msg-container"]'
    );
}
function _waQueryMessageRows(root) {
    return root.querySelectorAll(_waMsgRowSelector());
}
function _waRowIdFromEl(el) {
    if (!el) return '';
    var id = el.getAttribute('data-id');
    if (id) return id;
    var listItem = el.closest('div.focusable-list-item[data-id]');
    if (listItem) return listItem.getAttribute('data-id') || '';
    var conv = el.closest('[data-testid^="conv-msg-"][data-id]')
        || el.closest('div.xscbp6u[data-id]');
    if (conv) return conv.getAttribute('data-id') || '';
    var inner = el.querySelector('[data-id]');
    return inner ? (inner.getAttribute('data-id') || '') : '';
}
function _waFindScrollEl(panel) {
    function score(el) {
        if (!el || el.scrollHeight <= el.clientHeight + 2) return -1;
        var oy = window.getComputedStyle(el).overflowY;
        if (oy !== 'auto' && oy !== 'scroll' && oy !== 'overlay') return -1;
        var rows = _waQueryMessageRows(el).length;
        return rows * 10000 + el.scrollHeight;
    }
    var best = panel;
    var bestScore = score(panel);
    var pref = panel.querySelector('[tabindex="-1"]');
    if (pref) {
        var ps = score(pref);
        if (ps > bestScore) { bestScore = ps; best = pref; }
    }
    var tabPanel = panel.querySelector('div[tabindex="0"][data-tab]');
    if (tabPanel) {
        var ts = score(tabPanel);
        if (ts > bestScore) { bestScore = ts; best = tabPanel; }
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
function _waHasReverseFlex(node) {
    try {
        var fd = window.getComputedStyle(node).flexDirection || '';
        return fd.indexOf('reverse') >= 0;
    } catch (e) {}
    return false;
}
function _waDetectReversedOnce(panel, scrollEl) {
    if (panel.dataset.waReversed === '1') return true;
    if (panel.dataset.waReversed === '0') return false;

    var reversed = false;
    if (_waHasReverseFlex(scrollEl)) reversed = true;
    if (!reversed) {
        var kids = scrollEl.querySelectorAll(
            '[role="row"], [data-testid^="conv-msg-"], ' +
            '[data-testid="msg-container"], div.focusable-list-item'
        );
        for (var i = 0; i < kids.length && i < 24; i++) {
            if (_waHasReverseFlex(kids[i])) {
                reversed = true;
                break;
            }
        }
    }
    // Only use scroll position on first detection while still at the bottom.
    if (!reversed && scrollEl.scrollTop >= -2 && scrollEl.scrollTop <= 2) {
        var rows = _waQueryMessageRows(scrollEl);
        if (rows.length > 0) {
            var last = rows[rows.length - 1];
            var rect = last.getBoundingClientRect();
            var er = scrollEl.getBoundingClientRect();
            if (rect.bottom >= er.bottom - 40) reversed = true;
        }
    }
    panel.dataset.waReversed = reversed ? '1' : '0';
    return reversed;
}
function _waMaxScroll(el) {
    return Math.max(0, el.scrollHeight - el.clientHeight);
}
function _waLogicalTop(el, reversed) {
    if (reversed) {
        return _waMaxScroll(el) - el.scrollTop;
    }
    return el.scrollTop;
}
function _waSetLogicalTop(el, reversed, logical) {
    if (reversed) {
        var maxScroll = _waMaxScroll(el);
        el.scrollTop = Math.max(0, Math.min(maxScroll, maxScroll - logical));
    } else {
        el.scrollTop = Math.max(0, Math.min(_waMaxScroll(el), logical));
    }
}
function _waScrollPhysical(el, reversed, delta) {
    var maxScroll = _waMaxScroll(el);
    if (reversed) {
        el.scrollTop = Math.max(0, Math.min(maxScroll, el.scrollTop + delta));
    } else {
        el.scrollTop = Math.max(0, Math.min(maxScroll, el.scrollTop - delta));
    }
}
function _waIsNotificationRow(el) {
    if (!el) return false;
    var row = el.closest('[role="row"]') || el;
    if (row.querySelector(
        '[data-testid="msg-notification-container"], ' +
        '[data-testid="subtype-invite"], ' +
        '[data-testid="subtype-add"], ' +
        '[data-testid="system_message"]'
    )) {
        return true;
    }
    var container = row.querySelector('[data-testid="msg-container"]') || row;
    if (container.getAttribute('data-testid') === 'msg-notification-container') {
        return true;
    }
    return !container.querySelector('[data-testid="msg-meta"]')
        && !container.querySelector('[data-testid="tail-out"], [data-testid="tail-in"]');
}
function _waIsRealMessageRow(el) {
    if (!el || _waIsNotificationRow(el)) return false;
    var container = el.querySelector('[data-testid="msg-container"]') || el;
    return container.getAttribute('data-testid') !== 'msg-notification-container';
}
function _waMaxLogical(el) {
    return _waMaxScroll(el);
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
    var reversed = _waDetectReversedOnce(panel, scrollEl);
    var panelRect = panel.getBoundingClientRect();
    var rows = _waQueryMessageRows(panel);
    var visible = [];
    var maxBottom = -Infinity;
    var minTop = Infinity;
    var anchorBottom = null;
    var oldestId = '';
    var oldestTop = Infinity;
    var seenIds = {};

    for (var i = 0; i < rows.length; i++) {
        var el = rows[i];
        if (!_waIsRealMessageRow(el)) continue;
        var rect = el.getBoundingClientRect();
        if (rect.bottom <= panelRect.top + 1) continue;
        if (rect.top >= panelRect.bottom - 1) continue;

        var dataId = _waRowIdFromEl(el);
        if (!dataId || seenIds[dataId]) continue;
        seenIds[dataId] = true;
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

_JS_PANEL_SCROLL_UP = (
    _JS_SCROLL_HELPERS
    + r"""
return (function(panel, anchorId, overlap) {
    overlap = overlap || 32;
    var scrollEl = _waFindScrollEl(panel);
    var reversed = _waDetectReversedOnce(panel, scrollEl);
    var prevRaw = scrollEl.scrollTop;
    var prevLogical = _waLogicalTop(scrollEl, reversed);
    var stepSize;
    var anchorRow = null;

    if (anchorId) {
        anchorRow = panel.querySelector('[data-id="' + anchorId + '"]');
        if (!anchorRow) {
            anchorRow = panel.querySelector(
                '[data-testid="conv-msg-' + anchorId + '"]'
            );
        }
        if (!anchorRow) {
            anchorRow = panel.querySelector(
                'div.xscbp6u[data-id="' + anchorId + '"]'
            );
        }
    }

    // Move close to a full viewport per step, but keep the overlap larger than
    // the tallest currently-visible row so consecutive viewports are contiguous
    // and no row can fall between them unharvested.
    var clientH = scrollEl.clientHeight || 600;
    var panelRect = panel.getBoundingClientRect();
    var rows = _waQueryMessageRows(panel);
    var maxVisibleRow = 0;
    for (var i = 0; i < rows.length; i++) {
        var rowEl = rows[i];
        if (!_waIsRealMessageRow(rowEl)) continue;
        var rc = rowEl.getBoundingClientRect();
        if (rc.bottom <= panelRect.top + 1) continue;
        if (rc.top >= panelRect.bottom - 1) continue;
        if (rc.height > maxVisibleRow) maxVisibleRow = rc.height;
    }
    var safeOverlap = Math.max(overlap, maxVisibleRow + 48);
    stepSize = Math.floor(clientH * (0.6 + Math.random() * 0.12));
    if (stepSize > clientH - safeOverlap) {
        stepSize = clientH - safeOverlap;
    }
    if (stepSize < 80) stepSize = 80;

    var delta = stepSize;
    _waSetLogicalTop(scrollEl, reversed, Math.max(0, prevLogical - delta));

    var targets = [scrollEl];
    if (panel !== scrollEl) targets.push(panel);
    var wheelSign = reversed ? 1 : -1;
    var remaining = delta;
    while (remaining > 0) {
        var chunk = Math.min(remaining, 14 + Math.floor(Math.random() * 26));
        for (var t = 0; t < targets.length; t++) {
            _waDispatchWheel(targets[t], wheelSign * chunk);
        }
        remaining -= chunk;
    }

    var logicalTop = _waLogicalTop(scrollEl, reversed);
    var moved = Math.abs(logicalTop - prevLogical) >= 1
        || Math.abs(scrollEl.scrollTop - prevRaw) >= 1;
    return {
        scrolled: logicalTop - prevLogical,
        delta: delta,
        scrollTop: logicalTop,
        rawScrollTop: scrollEl.scrollTop,
        anchorFound: !!anchorRow,
        reversed: reversed,
        stuck: !moved
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
    var a = parseInt(m[1], 10);
    var b = parseInt(m[2], 10);
    var validMonthDay = function(mon, day) {
        return mon >= 1 && mon <= 12 && day >= 1 && day <= 31;
    };
    if (!validMonthDay(a, b) && !validMonthDay(b, a)) return false;
    if (m[3]) {
        var year = parseInt(m[3], 10);
        if (m[3].length === 2) return year >= 0 && year <= 99;
        return year >= 2000 && year <= 2100;
    }
    return true;
}
function _waDateListRowFromNode(node, listRoot) {
    if (!node || !listRoot) return null;
    var cur = node;
    while (cur && cur !== listRoot) {
        if (cur.parentElement === listRoot) return cur;
        if (_waMessageRowFromEl(cur)) return null;
        cur = cur.parentElement;
    }
    return null;
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
function _waMessageListItem(rowEl) {
    if (!rowEl) return rowEl;
    var conv = rowEl.closest('[data-testid^="conv-msg-"][data-id]');
    if (conv) return conv;
    var scbp = rowEl.closest('div.xscbp6u[data-id]');
    if (scbp) return scbp;
    var focusable = rowEl.closest('div.focusable-list-item[data-id]');
    if (focusable) return focusable;
    return rowEl;
}
function _waIsInsideMessage(el) {
    return !!el.closest(
        'div.focusable-list-item[data-id], ' +
        '[data-testid^="conv-msg-"][data-id], ' +
        '[data-testid="msg-container"], [data-testid="msg-meta"]'
    );
}
function _waMessageRowFromEl(el) {
    if (!el || el.nodeType !== 1) return null;
    if (el.getAttribute('data-testid') === 'msg-container') return el;
    var testid = el.getAttribute('data-testid') || '';
    if (testid.indexOf('conv-msg-') === 0 && el.getAttribute('data-id')) return el;
    if (el.classList.contains('focusable-list-item') && el.getAttribute('data-id')) return el;
    if (el.classList.contains('xscbp6u') && el.getAttribute('data-id')) return el;

    var nested = el.querySelector('[data-testid="msg-container"]')
        || el.querySelector('[data-testid^="conv-msg-"][data-id]')
        || el.querySelector('div.xscbp6u[data-id]')
        || el.querySelector('div.focusable-list-item[data-id]');
    if (nested) return nested;
    var conv = el.closest('[data-testid^="conv-msg-"][data-id]')
        || el.closest('div.xscbp6u[data-id]');
    if (conv) return conv;
    return null;
}
function _waFindMessageList(panel) {
    var scrollEl = _waFindScrollEl(panel);
    var rows = _waQueryMessageRows(panel);
    var sample = rows.length ? rows[0] : null;
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
        if (el.querySelector(_waMsgRowSelector())) {
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
        if (tab.querySelector(_waMsgRowSelector())) {
            continue;
        }
        if (tab.querySelector('a[href]')) continue;
        addCandidate(tab, _waDateLabelFromEl(tab));
    }

    var roleRows = panel.querySelectorAll('[role="row"]');
    for (var rr = 0; rr < roleRows.length; rr++) {
        var roleRow = roleRows[rr];
        if (roleRow.querySelector(_waMsgRowSelector())) continue;
        addCandidate(roleRow, _waDateLabelFromEl(roleRow));
    }

    var listRoot = _waFindMessageList(panel);
    var children = _waListChildren(listRoot);
    for (var c = 0; c < children.length; c++) {
        var child = children[c];
        if (_waMessageRowFromEl(child)) continue;
        addCandidate(child, _waDateLabelFromEl(child));
    }

    var dateSpans = listRoot.querySelectorAll('span[dir="auto"]');
    for (var ds = 0; ds < dateSpans.length; ds++) {
        var sp = dateSpans[ds];
        if (_waIsInsideMessage(sp)) continue;
        var label = _waElPlainText(sp);
        if (!_waLooksLikeDateLabel(label)) continue;
        var wrapper = _waDateListRowFromNode(sp, listRoot);
        if (wrapper) addCandidate(wrapper, label);
    }

    candidates.sort(function(a, b) { return a.top - b.top; });
    return candidates;
}
function _waTopmostVisibleMessageRow(panel) {
    var panelRect = panel.getBoundingClientRect();
    var rows = _waQueryMessageRows(panel);
    var topRow = null;
    var minTop = Infinity;

    for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        if (!_waIsRealMessageRow(row)) continue;
        if (!_waVisibleInPanel(row, panelRect)) continue;
        var top = row.getBoundingClientRect().top;
        if (top < minTop) {
            minTop = top;
            topRow = row;
        }
    }
    return topRow;
}
function _waDateForRow(panel, rowEl) {
    var listItem = _waMessageListItem(rowEl);
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
    return _waMessageListItem(row).getBoundingClientRect().top;
}
function _waRowKey(row) {
    return _waRowIdFromEl(row);
}
function _waVisibleMessageRows(panel) {
    var panelRect = panel.getBoundingClientRect();
    var rows = _waQueryMessageRows(panel);
    var seen = {};
    var items = [];

    for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        if (!_waIsRealMessageRow(row)) continue;
        if (!_waVisibleInPanel(row, panelRect)) continue;

        var key = _waRowKey(row);
        if (!key || seen[key]) continue;
        seen[key] = true;

        var listItem = row.closest('div.xscbp6u[data-id]')
            || row.closest('[data-testid^="conv-msg-"][data-id]')
            || row.closest('div.focusable-list-item[data-id]');
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

    var rows = _waQueryMessageRows(panel);
    for (var i = 0; i < rows.length; i++) {
        if (!_waIsRealMessageRow(rows[i])) continue;
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

_JS_DATE_FOR_ROW = (
    _JS_SCROLL_HELPERS
    + _JS_DATE_HELPERS
    + r"""
return (function(panel, rowEl) {
    return _waDateForRow(panel, rowEl);
})(arguments[0], arguments[1]);
"""
)

_JS_FIND_DATE_ABOVE = (
    _JS_SCROLL_HELPERS
    + _JS_DATE_HELPERS
    + r"""
return (function(panel) {
    return _waFindDateAbove(panel);
})(arguments[0]);
"""
)

_JS_TOPMOST_NEEDS_DATE = (
    _JS_SCROLL_HELPERS
    + _JS_DATE_HELPERS
    + r"""
return (function(panel) {
    var row = _waTopmostVisibleMessageRow(panel);
    if (!row) return false;
    return !_waDateForRow(panel, row);
})(arguments[0]);
"""
)

_JS_OLDER_MESSAGES_HELPERS = r"""
function _waOlderMessagesPatterns() {
    return [
        /click here to get older messages/i,
        /use whatsapp on your phone to see older/i,
        /get older messages from your phone/i,
        /older messages from your phone/i,
        /לחצו כאן כדי לקבל/i,
        /הודעות ישנות יותר/i,
        /הודעות צ'אט ישנות/i
    ];
}
function _waOlderMessagesButton(panel) {
    if (!panel) return null;
    var patterns = _waOlderMessagesPatterns();
    var candidates = panel.querySelectorAll('button');
    for (var i = 0; i < candidates.length; i++) {
        var el = candidates[i];
        var text = (el.innerText || el.textContent || '')
            .replace(/\s+/g, ' ')
            .trim();
        if (!text) continue;
        for (var p = 0; p < patterns.length; p++) {
            if (patterns[p].test(text)) return el;
        }
    }
    return null;
}
"""

_JS_HAS_OLDER_MESSAGES_PROMPT = (
    _JS_OLDER_MESSAGES_HELPERS
    + r"""
return !!_waOlderMessagesButton(arguments[0]);
"""
)

_JS_CLICK_OLDER_MESSAGES_PROMPT = (
    _JS_OLDER_MESSAGES_HELPERS
    + r"""
return (function(panel) {
    var btn = _waOlderMessagesButton(panel);
    if (!btn) return false;
    try {
        btn.scrollIntoView({block: 'center', inline: 'nearest'});
    } catch (e) {}
    try {
        btn.click();
        return true;
    } catch (e) {}
    try {
        btn.dispatchEvent(new MouseEvent('click', {
            bubbles: true,
            cancelable: true,
            view: window
        }));
        return true;
    } catch (e2) {}
    return false;
})(arguments[0]);
"""
)

