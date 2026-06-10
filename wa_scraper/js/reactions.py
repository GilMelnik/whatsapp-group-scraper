"""JavaScript snippets for reaction overlay parsing."""

_JS_FIRST_EMOJI = r"""
return (function(s) {
    var m = (s || '').match(/\p{Extended_Pictographic}/u);
    return m ? m[0] : '';
})(arguments[0]);
"""

_JS_REACTIONS_OVERLAY_HELPERS = r"""
function _waVisible(el) {
    if (!el) return false;
    try {
        var st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity || '1') === 0) {
            return false;
        }
        var r = el.getBoundingClientRect();
        return r.width > 20 && r.height > 20;
    } catch (e) {
        return false;
    }
}
function _waReactionParticipantSelector() {
    return (
        '[data-testid="react-participant"],'
        + '[data-testid="reactions-list-item"],'
        + '[data-testid="reactions-details-cell-not-me"],'
        + '[data-testid="reactions-details-cell-me"],'
        + '[data-testid="reactions-list"] [role="listitem"],'
        + '[role="listitem"][aria-label*="reacted"],'
        + '[role="dialog"] [data-testid="cell-frame-title"],'
        + '[data-testid="cell-frame-container"]'
    );
}
function _waOverlayHasParticipants(root) {
    if (!root) return false;
    return !!root.querySelector(_waReactionParticipantSelector());
}
function _waOverlayLooksLikeReactions(root) {
    if (!root || !_waVisible(root)) return false;
    if (_waOverlayHasParticipants(root)) return true;
    if (root.getAttribute && root.getAttribute('data-testid') === 'reactions-list') return true;
    if (root.querySelector && root.querySelector('[data-testid="reaction-count-header"]')) return true;
    return !!root.querySelector('[data-testid="reactions-tab"], [role="tablist"]');
}
function _waCountParticipants(root) {
    if (!root) return 0;
    return root.querySelectorAll(
        '[role="listitem"][aria-label*="reacted"],'
        + '[data-testid="reactions-details-cell-not-me"],'
        + '[data-testid="reactions-details-cell-me"],'
        + '[data-testid="react-participant"]'
    ).length;
}
function _waExpectedReactionCount(root) {
    if (!root) return 0;
    var header = root.querySelector('[data-testid="reaction-count-header"]');
    if (!header && root.getAttribute && root.getAttribute('data-testid') === 'reaction-count-header') {
        header = root;
    }
    if (!header) return 0;
    var txt = (header.innerText || header.textContent || '').trim();
    var m = txt.match(/(\d+)/);
    return m ? parseInt(m[1], 10) : 0;
}
function _waContainerFromReactionHeader(headerEl) {
    if (!headerEl) return null;
    var container = headerEl.parentElement;
    while (container && container !== document.body) {
        if (_waCountParticipants(container) > 0 && _waVisible(container)) {
            return container;
        }
        container = container.parentElement;
    }
    return headerEl.parentElement || null;
}
function _waAddReactionRootCandidate(candidates, seen, el) {
    if (!el || seen[el]) return;
    seen[el] = true;
    candidates.push(el);
}
function _waPickBestReactionsRoot(candidates) {
    var best = null;
    var bestScore = 0;
    for (var i = 0; i < candidates.length; i++) {
        var el = candidates[i];
        if (!el || !_waVisible(el)) continue;
        var count = _waCountParticipants(el);
        var expected = _waExpectedReactionCount(el);
        if (count <= 0 && expected <= 0 && !_waOverlayHasParticipants(el)) continue;
        var score = Math.max(count, expected);
        if (score > bestScore) {
            best = el;
            bestScore = score;
        }
    }
    return best;
}
function _waResolveReactionsRoot(dialog) {
    var candidates = [];
    var seen = {};

    if (dialog) {
        var chain = dialog;
        while (chain && chain !== document.body) {
            _waAddReactionRootCandidate(candidates, seen, chain);
            if (chain.getAttribute && chain.getAttribute('data-testid') === 'drawer-right') {
                break;
            }
            chain = chain.parentElement;
        }
        var headerFromDialog = dialog.closest('[data-testid="reaction-count-header"]');
        if (headerFromDialog) {
            _waAddReactionRootCandidate(
                candidates, seen, _waContainerFromReactionHeader(headerFromDialog)
            );
        }
        var drawer = dialog.closest('[data-testid="drawer-right"]');
        if (drawer) {
            _waAddReactionRootCandidate(candidates, seen, drawer);
        }
    }

    var headers = document.querySelectorAll('[data-testid="reaction-count-header"]');
    for (var h = headers.length - 1; h >= 0; h--) {
        if (!_waVisible(headers[h])) continue;
        _waAddReactionRootCandidate(
            candidates, seen, _waContainerFromReactionHeader(headers[h])
        );
    }

    var lists = document.querySelectorAll(
        '[data-testid="reactions-list"], [data-testid="drawer-right"]'
    );
    for (var l = lists.length - 1; l >= 0; l--) {
        _waAddReactionRootCandidate(candidates, seen, lists[l]);
    }

    var reactedRows = document.querySelectorAll('[role="listitem"][aria-label*="reacted"]');
    for (var r = reactedRows.length - 1; r >= 0; r--) {
        var row = reactedRows[r];
        var header = row.closest('[data-testid="reaction-count-header"]');
        if (header) {
            _waAddReactionRootCandidate(
                candidates, seen, _waContainerFromReactionHeader(header)
            );
            continue;
        }
        var drawerRow = row.closest('[data-testid="drawer-right"]');
        if (drawerRow) {
            _waAddReactionRootCandidate(candidates, seen, drawerRow);
            continue;
        }
        var parent = row.parentElement;
        while (parent && parent !== document.body) {
            if (_waCountParticipants(parent) > 1) {
                _waAddReactionRootCandidate(candidates, seen, parent);
                break;
            }
            if (parent.getAttribute && parent.getAttribute('role') === 'list') {
                _waAddReactionRootCandidate(candidates, seen, parent);
            }
            parent = parent.parentElement;
        }
    }

    var best = _waPickBestReactionsRoot(candidates);
    if (best) return best;

    var detailCell = document.querySelector(
        '[data-testid="reactions-details-cell-not-me"],'
        + '[data-testid="reactions-details-cell-me"],'
        + '[role="listitem"][aria-label*="reacted"]'
    );
    if (detailCell) {
        var parent = detailCell.parentElement;
        while (parent && parent !== document.body) {
            if (_waCountParticipants(parent) > 0 && _waVisible(parent)) {
                return parent;
            }
            parent = parent.parentElement;
        }
    }
    return dialog || null;
}
function _waShouldSkipParticipantRow(el) {
    if (!el || el.getAttribute('role') !== 'listitem') return false;
    var aria = (el.getAttribute('aria-label') || '');
    if (/\breacted\s+with\b/i.test(aria)) return false;
    return !!el.querySelector(
        '[data-testid="reactions-details-cell-not-me"],'
        + '[data-testid="reactions-details-cell-me"],'
        + '[data-testid="react-participant"]'
    );
}
function _waReactionScrollTargets(root) {
    var out = [];
    var seen = {};
    function add(el) {
        if (!el || seen[el]) return;
        seen[el] = true;
        out.push(el);
    }
    add(root.querySelector('[data-testid="scrollable-content"]'));
    add(root.querySelector('[data-scrolltraceid]'));
    add(root.querySelector('[data-testid="reactions-list"] [role="list"]'));
    add(root.querySelector('[data-testid="reactions-list"]'));
    add(root.querySelector('[role="list"] [data-testid^="list-item-"]') && root.querySelector('[role="list"] [data-testid^="list-item-"]').parentElement);
    add(root.querySelector('[role="list"]'));
    add(root);
    return out;
}
"""

_JS_FIND_REACTIONS_OVERLAY = (
    _JS_REACTIONS_OVERLAY_HELPERS
    + r"""
return (function(dialog) {
    return _waResolveReactionsRoot(dialog);
})(arguments[0]);
"""
)

_JS_HAS_REACTION_PARTICIPANTS = (
    _JS_REACTIONS_OVERLAY_HELPERS
    + r"""
return (function(dialog) {
    var root = _waResolveReactionsRoot(dialog);
    return _waOverlayHasParticipants(root);
})(arguments[0]);
"""
)

_JS_COLLECT_REACTORS_GROUPED = (
    _JS_REACTIONS_OVERLAY_HELPERS
    + r"""
return (function(dialog) {
    var grouped = {};
    var seenRows = {};

    function normEmoji(s) {
        return (s || '').replace(/[\uFE0E\uFE0F\u200D]/g, '');
    }
    function firstEmoji(s) {
        var m = (s || '').match(/\p{Extended_Pictographic}/u);
        return m ? m[0] : '';
    }
    function badName(n) {
        n = (n || '').trim();
        if (!n || n.length > 120) return true;
        if (/^\d+$/.test(n)) return true;
        if (/^(All|הכל|כל)$/i.test(n)) return true;
        if (/^[\p{Extended_Pictographic}\p{Emoji}\u200d\uFE0F\s·•,]+$/u.test(n)) return true;
        return false;
    }
    function cleanName(n) {
        n = (n || '').trim();
        n = n.replace(/^~\s*/, '');
        n = n.replace(/^Maybe\s+/i, '');
        n = n.replace(
            /,\s*(\+\d[\d\s\-()]{7,}\d|\d[\d\s\-()]{8,}\d)\s*$/,
            ''
        );
        return n.trim();
    }
    function normKey(n) {
        return cleanName(n).toLowerCase().replace(/\s+/g, ' ');
    }
    function phoneFromText(s) {
        var m = (s || '').match(/(\+\d[\d\s\-()]{7,}\d|\d[\d\s\-()]{8,}\d)/);
        return m ? m[1].trim() : '';
    }
    function rowEmoji(el) {
        if (!el) return '';
        var ariaLabel = (el.getAttribute('aria-label') || '').trim();
        var ariaMatch = ariaLabel.match(/\breacted\s+with\s+(.+)$/i);
        if (ariaMatch) {
            var fromAria = firstEmoji(ariaMatch[1]) || ariaMatch[1].trim();
            if (fromAria) return fromAria;
        }
        var reactionImgs = el.querySelectorAll(
            '[data-testid="reaction-bubble-item"] img[alt],'
            + '[data-testid="reactions-list-item"] img[alt],'
            + 'img.emojik[alt], img[alt].emojik,'
            + 'img.emoji[alt], img[alt].emoji'
        );
        for (var r = reactionImgs.length - 1; r >= 0; r--) {
            var em = firstEmoji(reactionImgs[r].getAttribute('alt') || '');
            if (em) return em;
        }
        return firstEmoji(el.innerText || el.textContent || '');
    }
    function reactorKey(userName, phone) {
        var nameKey = normKey(userName || '');
        if (nameKey) return nameKey;
        return (phone || '').replace(/\s+/g, '');
    }
    function phoneFromSecondary(el) {
        var secondary = el.querySelector(
            '[data-testid="cell-frame-secondary"],'
            + '[data-testid="cell-frame-subtitle"]'
        );
        if (!secondary) return '';
        var sec = (
            secondary.getAttribute('title')
            || secondary.innerText
            || secondary.textContent
            || ''
        ).trim();
        return phoneFromText(sec) || sec;
    }
    function reactorFromParticipantRow(row) {
        if (!row) return null;
        var ariaLabel = (row.getAttribute('aria-label') || '').trim();
        var titleEl = row.querySelector(
            '[data-testid="cell-frame-title"] span[title],'
            + '[data-testid="cell-frame-title"] span[dir],'
            + '[data-testid="cell-frame-primary"] span[title],'
            + '[data-testid="cell-frame-primary"] span[dir="auto"]'
        );
        var userName = '';
        if (titleEl) {
            userName = cleanName(
                titleEl.getAttribute('title')
                || titleEl.innerText
                || titleEl.textContent
                || ''
            );
        }
        var phone = phoneFromSecondary(row) || '';
        if (!userName && !phone && ariaLabel) {
            var reactedMatch = ariaLabel.match(/^(.+?)\s+reacted\s+with\b/i);
            if (reactedMatch) {
                var raw = reactedMatch[1].trim();
                phone = phoneFromText(raw);
                userName = cleanName(raw);
            }
        }
        if (badName(userName) && !phone) return null;
        if (looks_like_phone(userName)) {
            phone = phone || userName;
            userName = '';
        }
        return { user_name: userName, phone: phone || null };
    }
    function looks_like_phone(text) {
        return /^(\+\d[\d\s\-()]{7,}\d|\d[\d\s\-()]{8,}\d)$/.test((text || '').trim());
    }
    function addToGroup(em, reactor) {
        em = (em || '').trim();
        if (!em) return;
        if (!reactor) return;
        var userName = cleanName(reactor.user_name || '');
        var phone = (reactor.phone || '').trim();
        if (badName(userName) && !phone) return;
        var key = normEmoji(em) || em;
        if (!grouped[key]) grouped[key] = { emoji: em, senders: [], seen: {} };
        var nameKey = reactorKey(userName, phone);
        if (!nameKey || grouped[key].seen[nameKey]) return;
        grouped[key].seen[nameKey] = true;
        grouped[key].senders.push({
            user_name: userName,
            phone: phone || null
        });
    }
    function collectParticipantRow(row) {
        if (!row || _waShouldSkipParticipantRow(row)) return;
        var rowKey = row.getAttribute('aria-label') || row.getAttribute('data-testid') || String(row);
        if (seenRows[rowKey]) return;
        seenRows[rowKey] = true;
        var em = rowEmoji(row);
        var reactor = reactorFromParticipantRow(row);
        if (reactor && em) addToGroup(em, reactor);
    }
    function collect(root) {
        if (!root) return;
        var ariaRows = root.querySelectorAll('[role="listitem"][aria-label*="reacted"]');
        for (var i = 0; i < ariaRows.length; i++) {
            collectParticipantRow(ariaRows[i]);
        }
        if (ariaRows.length > 0) return;
        var rows = root.querySelectorAll(
            '[data-testid="reactions-details-cell-not-me"],'
            + '[data-testid="reactions-details-cell-me"],'
            + '[data-testid="react-participant"]'
        );
        for (var j = 0; j < rows.length; j++) {
            var row = rows[j];
            var ariaRow = row.closest('[role="listitem"][aria-label*="reacted"]') || row;
            collectParticipantRow(ariaRow);
        }
    }

    var root = _waResolveReactionsRoot(dialog);
    if (!root) return [];

    collect(root);

    var scrollTargets = _waReactionScrollTargets(root);
    for (var s = 0; s < scrollTargets.length; s++) {
        var scrollEl = scrollTargets[s];
        collect(scrollEl);
        var lastTop = -1;
        for (var attempt = 0; attempt < 50; attempt++) {
            scrollEl.scrollTop += Math.max(120, Math.floor(scrollEl.clientHeight * 0.6));
            if (scrollEl.scrollTop === lastTop) break;
            lastTop = scrollEl.scrollTop;
            collect(scrollEl);
        }
    }

    var out = [];
    for (var k in grouped) {
        if (!grouped.hasOwnProperty(k)) continue;
        var g = grouped[k];
        out.push({ emoji: g.emoji, count: g.senders.length, senders: g.senders });
    }
    return out;
})(arguments[0]);
"""
)

