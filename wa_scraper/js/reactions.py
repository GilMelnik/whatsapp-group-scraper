"""JavaScript snippets for reaction overlay parsing."""

from wa_scraper.js.message import _JS_HELPERS

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

    function allEmojis(s) {
        var out = [];
        var re = /\p{Extended_Pictographic}/gu;
        var m;
        while ((m = re.exec(s || '')) !== null) {
            if (out.indexOf(m[0]) === -1) out.push(m[0]);
        }
        return out;
    }

    function parseCount(s) {
        var m = (s || '').match(/\d+/);
        return m ? parseInt(m[0], 10) : 1;
    }

    function emojisFromBubble(btn) {
        var emojis = [];
        var items = btn.querySelectorAll(
            '[data-testid="reaction-bubble-item"] img[alt], img[alt]'
        );
        for (var i = 0; i < items.length; i++) {
            var alt = (items[i].getAttribute('alt') || '').trim();
            var em = firstEmoji(alt) || alt;
            if (em && emojis.indexOf(em) === -1) emojis.push(em);
        }
        if (emojis.length === 0) {
            var lbl = btn.getAttribute('aria-label') || '';
            var txt = (btn.innerText || btn.textContent || '').trim();
            emojis = allEmojis(lbl + ' ' + txt);
        }
        return emojis;
    }

    function scanReactionBubbles(root) {
        if (!root) return;
        var bubbles = root.querySelectorAll('[data-testid="reaction-bubble"]');
        for (var b = 0; b < bubbles.length; b++) {
            var btn = bubbles[b];
            var emojis = emojisFromBubble(btn);
            if (emojis.length === 0) continue;
            var totalCount = parseCount((btn.innerText || btn.textContent || ''));
            if (emojis.length === 1) {
                add(emojis[0], totalCount || 1, btn);
            } else {
                for (var k = 0; k < emojis.length; k++) {
                    add(emojis[k], 1, btn);
                }
            }
        }
    }

    function scanRoot(root) {
        if (!root) return;
        scanReactionBubbles(root);
        var containers = root.querySelectorAll(
            '[data-testid="reactions-read-more-container"],' +
            '[data-testid="reactions-container"],' +
            '[data-testid="msg-reactions"],' +
            '[data-testid="reactions-bubble"],' +
            '[data-testid="addon-bubble-container"]'
        );
        for (var c = 0; c < containers.length; c++) {
            var container = containers[c];
            if (container.querySelector('[data-testid="reaction-bubble"]')) continue;
            var pills = container.querySelectorAll('[role="button"], button');
            for (var p = 0; p < pills.length; p++) {
                var pill = pills[p];
                var txt = (pill.innerText || pill.textContent || '').trim();
                if (!txt) continue;
                var emojis = allEmojis(txt);
                if (emojis.length === 0) continue;
                var count = parseCount(txt);
                if (count < 1) count = 1;
                if (emojis.length === 1) {
                    add(emojis[0], count, pill);
                } else {
                    for (var e = 0; e < emojis.length; e++) {
                        add(emojis[e], 1, pill);
                    }
                }
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
    if (root.getAttribute && root.getAttribute('role') === 'dialog') {
        return !!root.querySelector(
            '[data-testid="reactions-details-cell-not-me"],'
            + '[data-testid="reactions-details-cell-me"],'
            + '[role="listitem"][aria-label*="reacted"]'
        );
    }
    return !!root.querySelector('[data-testid="reactions-tab"], [role="tablist"]');
}
function _waResolveReactionsRoot(dialog) {
    if (dialog) {
        var chain = dialog;
        while (chain) {
            if (_waOverlayLooksLikeReactions(chain)) return chain;
            if (chain.getAttribute && chain.getAttribute('data-testid') === 'drawer-right') {
                return chain;
            }
            chain = chain.parentElement;
        }
        var drawer = dialog.closest('[data-testid="drawer-right"]');
        if (drawer && _waVisible(drawer)) return drawer;
    }
    var nodes = document.querySelectorAll(
        '[data-testid="drawer-right"],'
        + '[data-testid="reactions-list"],'
        + '[role="dialog"],'
        + '[role="list"]'
    );
    for (var i = nodes.length - 1; i >= 0; i--) {
        if (_waOverlayLooksLikeReactions(nodes[i])) return nodes[i];
    }
    var detailCell = document.querySelector(
        '[data-testid="reactions-details-cell-not-me"],'
        + '[data-testid="reactions-details-cell-me"],'
        + '[role="listitem"][aria-label*="reacted"]'
    );
    if (detailCell) {
        return (
            detailCell.closest('[role="dialog"]')
            || detailCell.closest('[role="list"]')
            || detailCell.closest('[data-testid="drawer-right"]')
            || detailCell
        );
    }
    return dialog || null;
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

_JS_COLLECT_REACTORS = (
    _JS_REACTIONS_OVERLAY_HELPERS
    + r"""
return (function(dialog, targetEmoji) {
    var names = [];
    var seen = {};
    targetEmoji = targetEmoji || '';

    function normEmoji(s) {
        return (s || '').replace(/[\uFE0E\uFE0F\u200D]/g, '');
    }
    function emojiMatches(left, right) {
        left = normEmoji(left);
        right = normEmoji(right);
        if (!left || !right) return !right;
        return left.indexOf(right) >= 0 || right.indexOf(left) >= 0;
    }
    function firstEmoji(s) {
        var m = (s || '').match(/\p{Extended_Pictographic}/u);
        return m ? m[0] : '';
    }
    function isTab(el) {
        return !!(el && el.closest(
            '[data-testid="reactions-tab"], [role="tab"], [role="tablist"]'
        ));
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
    function addName(n) {
        n = cleanName(n);
        if (badName(n)) return;
        var key = normKey(n);
        if (seen[key]) return;
        seen[key] = true;
        names.push(n);
    }
    function shouldSkipParticipantRow(el) {
        if (!el || el.getAttribute('role') !== 'listitem') return false;
        return !!el.querySelector(
            '[data-testid="reactions-details-cell-not-me"],'
            + '[data-testid="reactions-details-cell-me"],'
            + '[data-testid="react-participant"]'
        );
    }
    function rowEmoji(el) {
        if (!el) return '';
        var reactionImgs = el.querySelectorAll(
            '[data-testid="reaction-bubble-item"] img[alt],'
            + '[data-testid="reactions-list-item"] img[alt]'
        );
        for (var r = reactionImgs.length - 1; r >= 0; r--) {
            var altR = (reactionImgs[r].getAttribute('alt') || '').trim();
            var emR = firstEmoji(altR);
            if (emR) return emR;
        }
        return firstEmoji(el.innerText || el.textContent || '');
    }
    function nameFromAriaLabel(el) {
        var ariaLabel = (el.getAttribute('aria-label') || '').trim();
        var reactedMatch = ariaLabel.match(/^(.+?)\s+reacted\s+with\b/i);
        if (reactedMatch) {
            addName(reactedMatch[1]);
            return true;
        }
        return false;
    }
    function nameFromRow(el) {
        if (!el || isTab(el)) return;
        var em = rowEmoji(el);
        if (targetEmoji && em && !emojiMatches(em, targetEmoji)) return;

        var titleEl = el.querySelector(
            '[data-testid="react-participant"] [data-testid="cell-frame-title"] span[title],'
            + '[data-testid="react-participant"] span[title],'
            + '[data-testid="cell-frame-title"] span[title],'
            + '[data-testid="cell-frame-title"] span[dir],'
            + '[data-testid="cell-frame-primary"] span[title],'
            + '[data-testid="cell-frame-primary"] span[dir="auto"]'
        );
        if (titleEl) {
            var n = cleanName(
                titleEl.getAttribute('title')
                || titleEl.innerText
                || titleEl.textContent
                || ''
            );
            if (!badName(n)) { addName(n); return; }
        }

        if (nameFromAriaLabel(el)) return;

        var secondary = el.querySelector(
            '[data-testid="cell-frame-secondary"],'
            + '[data-testid="cell-frame-subtitle"]'
        );
        if (secondary) {
            var sec = cleanName(
                secondary.getAttribute('title')
                || secondary.innerText
                || secondary.textContent
                || ''
            );
            if (!badName(sec)) { addName(sec); return; }
        }

        var picks = el.querySelectorAll('span[title], span[dir="auto"], span[dir="ltr"], span[dir="rtl"]');
        for (var i = 0; i < picks.length; i++) {
            var pick = picks[i];
            if (isTab(pick)) continue;
            if (pick.closest('[data-testid="reactions-tab"], [role="tablist"]')) continue;
            n = cleanName(pick.getAttribute('title') || pick.innerText || pick.textContent || '');
            if (!badName(n)) { addName(n); return; }
        }

        var phone = phoneFromText(el.innerText || el.textContent || '');
        if (phone && !seen[phone]) {
            seen[phone] = true;
            names.push(phone);
            return;
        }

        var lines = (el.innerText || el.textContent || '').split('\n');
        for (var j = 0; j < lines.length; j++) {
            var line = cleanName(lines[j]);
            if (!badName(line)) { addName(line); return; }
        }
    }
    function collect(root) {
        if (!root) return;
        var rows = root.querySelectorAll(
            '[data-testid="react-participant"],'
            + '[data-testid="reactions-list-item"],'
            + '[data-testid="reactions-details-cell-not-me"],'
            + '[data-testid="reactions-details-cell-me"],'
            + '[data-testid="reactions-list"] [role="listitem"],'
            + '[role="listitem"][aria-label*="reacted"],'
            + '[role="listitem"],'
            + '[data-testid="cell-frame-container"]'
        );
        for (var i = 0; i < rows.length; i++) {
            if (shouldSkipParticipantRow(rows[i])) continue;
            nameFromRow(rows[i]);
        }
    }

    var root = _waResolveReactionsRoot(dialog);
    if (!root) return names;

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
    return names;
})(arguments[0], arguments[1]);
"""
)

_JS_PARSE_REACTION_TABS = r"""
return (function(dialog) {
    var results = [];
    var seen = {};

    function firstEmoji(s) {
        var m = (s || '').match(/\p{Extended_Pictographic}/u);
        return m ? m[0] : '';
    }
    function normEmoji(s) {
        return (s || '').replace(/[\uFE0E\uFE0F\u200D]/g, '');
    }
    function parseCount(s) {
        var m = (s || '').match(/\d+/);
        return m ? parseInt(m[0], 10) : 1;
    }
    function tabText(tab) {
        return (
            tab.getAttribute('aria-label')
            || tab.getAttribute('title')
            || tab.innerText
            || tab.textContent
            || ''
        ).trim();
    }
    function isAllTab(txt) {
        return /^(All|הכל|כל(?:\s+\d+)?|\d+\s+(?:reactions|תגובות))/i.test(txt);
    }

    var root = dialog.closest('[data-testid="drawer-right"]') || dialog;
    var tabs = root.querySelectorAll('[data-testid="reactions-tab"], [role="tab"]');
    for (var i = 0; i < tabs.length; i++) {
        var tab = tabs[i];
        var txt = tabText(tab);
        if (!txt || isAllTab(txt)) continue;
        var emoji = firstEmoji(txt);
        if (!emoji) {
            var img = tab.querySelector('img[alt]');
            if (img) emoji = firstEmoji(img.getAttribute('alt') || '') || (img.getAttribute('alt') || '').trim();
        }
        if (!emoji) continue;
        var key = normEmoji(emoji);
        if (seen[key]) continue;
        seen[key] = true;
        var count = parseCount(txt.replace(emoji, ''));
        if (count < 1) count = 1;
        results.push({ emoji: emoji, count: count });
    }
    return results;
})(arguments[0]);
"""

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
    function shouldSkipParticipantRow(el) {
        if (!el || el.getAttribute('role') !== 'listitem') return false;
        return !!el.querySelector(
            '[data-testid="reactions-details-cell-not-me"],'
            + '[data-testid="reactions-details-cell-me"],'
            + '[data-testid="react-participant"]'
        );
    }
    function rowEmoji(el) {
        if (!el) return '';
        var reactionImgs = el.querySelectorAll(
            '[data-testid="reaction-bubble-item"] img[alt],'
            + '[data-testid="reactions-list-item"] img[alt]'
        );
        for (var r = reactionImgs.length - 1; r >= 0; r--) {
            var em = firstEmoji(reactionImgs[r].getAttribute('alt') || '');
            if (em) return em;
        }
        return firstEmoji(el.innerText || el.textContent || '');
    }
    function nameFromAriaLabel(el) {
        var ariaLabel = (el.getAttribute('aria-label') || '').trim();
        var reactedMatch = ariaLabel.match(/^(.+?)\s+reacted\s+with\b/i);
        return reactedMatch ? cleanName(reactedMatch[1]) : '';
    }
    function nameFromRow(el) {
        if (!el) return '';
        var titleEl = el.querySelector(
            '[data-testid="cell-frame-title"] span[title],'
            + '[data-testid="cell-frame-title"] span[dir],'
            + '[data-testid="cell-frame-primary"] span[title],'
            + '[data-testid="cell-frame-primary"] span[dir="auto"],'
            + 'span[title], span[dir="auto"], span[dir="ltr"], span[dir="rtl"]'
        );
        if (titleEl) {
            var n = cleanName(
                titleEl.getAttribute('title')
                || titleEl.innerText
                || titleEl.textContent
                || ''
            );
            if (!badName(n)) return n;
        }
        var fromAria = nameFromAriaLabel(el);
        if (fromAria && !badName(fromAria)) return fromAria;
        var phone = phoneFromText(el.innerText || el.textContent || '');
        if (phone) return phone;
        var lines = (el.innerText || el.textContent || '').split('\n');
        for (var j = 0; j < lines.length; j++) {
            var line = cleanName(lines[j]);
            if (!badName(line)) return line;
        }
        return '';
    }
    function addToGroup(em, n) {
        em = em || 'unknown';
        n = cleanName(n);
        if (!n || badName(n)) return;
        var key = normEmoji(em) || em;
        if (!grouped[key]) grouped[key] = { emoji: em, senders: [], seen: {} };
        var nameKey = normKey(n);
        if (grouped[key].seen[nameKey]) return;
        grouped[key].seen[nameKey] = true;
        grouped[key].senders.push(n);
    }
    function collect(root) {
        if (!root) return;
        var rows = root.querySelectorAll(
            '[data-testid="react-participant"],'
            + '[data-testid="reactions-list-item"],'
            + '[data-testid="reactions-details-cell-not-me"],'
            + '[data-testid="reactions-details-cell-me"],'
            + '[data-testid="reactions-list"] [role="listitem"],'
            + '[role="listitem"][aria-label*="reacted"],'
            + '[role="listitem"],'
            + '[data-testid="cell-frame-container"]'
        );
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            if (shouldSkipParticipantRow(row)) continue;
            var rowKey = row.innerText || row.textContent || String(i);
            if (seenRows[rowKey]) continue;
            seenRows[rowKey] = true;
            var em = rowEmoji(row);
            var n = nameFromRow(row);
            if (n) addToGroup(em, n);
        }
    }

    var root = _waResolveReactionsRoot(dialog);
    if (!root) return [];

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
        if (g.emoji === 'unknown') continue;
        out.push({ emoji: g.emoji, count: g.senders.length, senders: g.senders });
    }
    return out;
})(arguments[0]);
"""
)

