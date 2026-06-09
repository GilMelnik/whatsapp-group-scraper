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

