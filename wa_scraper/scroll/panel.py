"""Chat message panel scrolling and metrics."""

from __future__ import annotations

import time

from selenium.common.exceptions import StaleElementReferenceException

from wa_scraper.js.scroll import (
    _JS_CLICK_OLDER_MESSAGES_PROMPT,
    _JS_HAS_OLDER_MESSAGES_PROMPT,
    _JS_OLDER_MESSAGES_PROMPT_KIND,
    _JS_PANEL_METRICS,
    _JS_PANEL_SCROLL_UP,
    _JS_SCROLL_HELPERS,
)
from wa_scraper.selectors import S
from wa_scraper.utils import find, scroll_by


class PanelScroller:
    """Scrolls the WhatsApp conversation panel and reports geometry."""

    def __init__(self, driver, panel) -> None:
        self.driver = driver
        self.panel = panel

    def wait_no_spinner(self, timeout: float = 8.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not find(self.panel, S["loading_spin"]):
                return
            time.sleep(0.3)

    def has_older_messages_prompt(self) -> bool:
        try:
            return bool(
                self.driver.execute_script(
                    _JS_HAS_OLDER_MESSAGES_PROMPT, self.panel
                )
            )
        except Exception:
            return False

    def older_messages_prompt_kind(self) -> str:
        """Return 'load', 'retry', or '' for the phone-sync prompt currently shown."""
        try:
            result = self.driver.execute_script(
                _JS_OLDER_MESSAGES_PROMPT_KIND, self.panel
            )
            if isinstance(result, str):
                return result
        except Exception:
            pass
        return ""

    def click_older_messages_prompt(self) -> bool:
        try:
            return bool(
                self.driver.execute_script(
                    _JS_CLICK_OLDER_MESSAGES_PROMPT, self.panel
                )
            )
        except Exception:
            return False

    def metrics(self) -> dict:
        try:
            result = self.driver.execute_script(_JS_PANEL_METRICS, self.panel)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        return {}

    def scroll_chunk(self, px: int) -> None:
        try:
            self.driver.execute_script(
                _JS_SCROLL_HELPERS
                + r"""
                var panel = arguments[0];
                var px = arguments[1];
                var scrollEl = _waFindScrollEl(panel);
                var targets = [scrollEl];
                if (panel !== scrollEl) targets.push(panel);
                var reversed = _waDetectReversedOnce(panel, scrollEl);
                var remaining = Math.abs(px);
                var up = px < 0;
                while (remaining > 0) {
                    var chunk = Math.min(remaining, 90 + Math.floor(Math.random() * 80));
                    for (var i = 0; i < targets.length; i++) {
                        _waScrollPhysical(targets[i], reversed, up ? chunk : -chunk);
                        var wheelSign = (reversed ? 1 : -1) * (up ? 1 : -1);
                        _waDispatchWheel(targets[i], wheelSign * chunk);
                    }
                    remaining -= chunk;
                }
                """,
                self.panel,
                px,
            )
        except Exception:
            scroll_by(self.driver, self.panel, px)

    def scroll_fallback(self, direction: str) -> None:
        """Stronger upward nudge when the main scroll-up step reports stuck."""
        if direction != "up":
            return
        try:
            self.driver.execute_script(
                _JS_SCROLL_HELPERS
                + r"""
                var panel = arguments[0];
                var scrollEl = _waFindScrollEl(panel);
                var reversed = _waDetectReversedOnce(panel, scrollEl);
                var prevLogical = _waLogicalTop(scrollEl, reversed);
                var delta = 120 + Math.floor(Math.random() * 96);
                _waSetLogicalTop(scrollEl, reversed, Math.max(0, prevLogical - delta));
                var targets = [scrollEl];
                if (panel !== scrollEl) targets.push(panel);
                var wheelSign = reversed ? 1 : -1;
                var remaining = delta;
                while (remaining > 0) {
                    var chunk = Math.min(remaining, 12 + Math.floor(Math.random() * 20));
                    for (var i = 0; i < targets.length; i++) {
                        _waDispatchWheel(targets[i], wheelSign * chunk);
                    }
                    remaining -= chunk;
                }
                """,
                self.panel,
            )
        except Exception:
            pass

    def scroll_up_from_anchor(
        self, anchor_id: str | None = None, overlap: int = 36
    ) -> dict:
        try:
            result = self.driver.execute_script(
                _JS_PANEL_SCROLL_UP, self.panel, anchor_id or "", overlap
            )
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        return {
            "scrolled": 0,
            "stuck": True,
            "atBottom": False,
            "atTop": False,
            "anchorFound": False,
        }

    @staticmethod
    def reached_history_top(
        metrics: dict,
        *,
        stall_streak: int,
        scroll_stuck_streak: int,
        no_new_streak: int,
        prev_oldest_id: str,
        has_older_messages_prompt: bool = False,
    ) -> bool:
        if has_older_messages_prompt:
            return False

        oldest_id = metrics.get("oldestId") or ""

        if not oldest_id or oldest_id != prev_oldest_id:
            return False
        if no_new_streak < 4:
            return False
        if stall_streak < 6:
            return False
        if scroll_stuck_streak < 3:
            return False

        return True

    def topmost_anchor_id(
        self, messages: dict, metrics: dict | None = None
    ) -> str | None:
        if metrics is None:
            metrics = self.metrics()
        visible = metrics.get("visible") or []
        if not visible:
            return metrics.get("oldestId") or None

        visible = sorted(visible, key=lambda item: item.get("top", 0))
        for item in visible:
            mid = item.get("id") or ""
            if mid and mid in messages:
                return mid
        for item in visible:
            mid = item.get("id") or ""
            if mid:
                return mid

        return metrics.get("oldestId") or None
