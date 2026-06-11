"""Harvest messages from the visible chat panel viewport."""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Tuple

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from wa_scraper.extraction.message import MessageExtractor
from wa_scraper.js.scroll import (
    _JS_CHAT_ITEMS,
    _JS_DATE_FOR_ROW,
    _JS_FIND_DATE_ABOVE,
    _JS_TOPMOST_NEEDS_DATE,
)
from wa_scraper.localization import is_date_label, normalize_date_impl
from wa_scraper.models import Message
from wa_scraper.postprocess import (
    fill_grouped_senders,
    parse_chat_date,
    sort_messages,
)
from wa_scraper.scroll.panel import PanelScroller
from wa_scraper.selectors import CHAT_ITEM_SELECTOR, S
from wa_scraper.storage import ShardWriter
from wa_scraper.utils import find, jitter, log


class MessageHarvester:
    """Scrolls through a chat and collects all messages into a dict by id."""

    def __init__(
        self,
        driver,
        scroll_pause: float = 1.0,
        max_top_scrolls: int = 6000,
        scroll_to_chat_start: bool = False,
        writer: Optional[ShardWriter] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        checkpoint_interval: int = 25,
    ) -> None:
        self.driver = driver
        self.scroll_pause = scroll_pause
        self.max_top_scrolls = max_top_scrolls
        self.scroll_to_chat_start = scroll_to_chat_start
        self.writer = writer
        self.from_date = from_date
        self.to_date = to_date
        self.checkpoint_interval = max(1, int(checkpoint_interval))
        self._extractor = MessageExtractor(driver)
        self._dates_logged = False

    def _panel_items(self, panel):
        try:
            return panel.find_elements(By.CSS_SELECTOR, CHAT_ITEM_SELECTOR)
        except StaleElementReferenceException:
            return []

    def _visible_chat_items(self, panel) -> list:
        try:
            result = self.driver.execute_script(_JS_CHAT_ITEMS, panel)
            if isinstance(result, list):
                return result
        except Exception:
            pass
        return []

    def _normalize_date(self, raw: str) -> str:
        raw = raw.strip()
        if not raw or not is_date_label(raw):
            return ""
        return normalize_date_impl(raw)

    def _find_date_above(self, panel) -> str:
        try:
            raw = self.driver.execute_script(_JS_FIND_DATE_ABOVE, panel)
            if isinstance(raw, str) and raw.strip():
                return self._normalize_date(raw)
        except Exception:
            pass
        return ""

    def _date_for_row(self, panel, row_el) -> str:
        try:
            raw = self.driver.execute_script(_JS_DATE_FOR_ROW, panel, row_el)
            if isinstance(raw, str) and raw.strip():
                return self._normalize_date(raw)
        except Exception:
            pass
        return ""

    def _resolve_date(
        self,
        panel,
        row_el,
        walk_date: str,
        *,
        separator_seen_above: bool,
    ) -> str:
        row_date = self._date_for_row(panel, row_el)
        if row_date:
            return row_date
        if separator_seen_above and walk_date:
            return walk_date
        return ""

    def _topmost_visible_needs_date(self, panel) -> bool:
        try:
            return bool(
                self.driver.execute_script(_JS_TOPMOST_NEEDS_DATE, panel)
            )
        except Exception:
            return False

    def _ensure_date_context(
        self, panel, scroller, current_date: str, *, max_steps: int = 120
    ) -> str:
        """Scroll up with the panel scroller until a date card applies to the top rows."""
        for _ in range(max_steps):
            found = self._find_date_above(panel)
            if found:
                return found
            if not self._topmost_visible_needs_date(panel):
                return current_date

            scroller.wait_no_spinner()
            scroll_result = scroller.scroll_up_from_anchor(None)
            if scroll_result.get("stuck"):
                scroller.scroll_fallback("up")
            jitter(0.15, 0.35)

        return self._find_date_above(panel) or current_date

    def _try_load_older_from_phone(self, panel, scroller) -> str:
        """Click WhatsApp's phone-sync prompt and wait for older messages.

        Returns the kind of prompt that was clicked: 'load' for the normal
        "load older messages from phone" prompt, 'retry' for the
        "Couldn't get older messages … click here to try again" error prompt,
        or '' if no prompt was clicked.
        """
        if not self.scroll_to_chat_start:
            return ""
        kind = scroller.older_messages_prompt_kind()
        if not scroller.click_older_messages_prompt():
            return ""

        if kind == "retry":
            log("  ⟳  Phone-sync error — clicked 'try again' …")
            # Give the phone extra time to re-deliver after a sync failure.
            scroller.wait_no_spinner(timeout=45.0)
            jitter(1.5, 3.0)
        else:
            log("  ↻  Clicked 'load older messages from phone' — waiting …")
            scroller.wait_no_spinner(timeout=45.0)
            jitter(0.8, 1.6)
        return kind or "load"

    def _reacquire_panel(self):
        """Re-locate the conversation panel after WhatsApp re-renders the DOM.

        WhatsApp periodically rebuilds the message-list container while loading
        large history chunks, which detaches the cached ``panel`` element and
        makes every subsequent JS call raise ``StaleElementReferenceException``.
        Re-finding the panel and rebuilding the scroller recovers from this.
        """
        panel = self.driver.find_element(By.CSS_SELECTOR, S["panel"])
        scroller = PanelScroller(self.driver, panel)
        try:
            self.driver.execute_script(
                "delete arguments[0].dataset.waReversed;", panel
            )
        except Exception:
            pass
        return panel, scroller

    def _nudge_to_trigger_phone_load(self, panel, scroller) -> None:
        """Scroll down then back up to retrigger WhatsApp's lazy loader.

        At the boundary of locally-loaded history, clicking the "load older
        messages from phone" prompt does not fetch anything until a real scroll
        gesture happens. This mimics a manual "scroll down a bit, then back up"
        so the loader sentinel leaves and re-enters the viewport.
        """
        log("  ↕  Nudging scroll to retrigger phone load …")
        scroller.scroll_chunk(700)   # down (toward newer)
        jitter(0.5, 0.9)
        scroller.scroll_chunk(-900)  # back up past the boundary
        scroller.wait_no_spinner(timeout=30.0)
        jitter(0.8, 1.5)

    def _ordered_panel_rows(self, panel) -> List:
        rows: List = []
        seen: set = set()

        for item in self._panel_items(panel):
            try:
                testid = item.get_attribute("data-testid") or ""
                if testid == "msg-container":
                    row_el = item
                elif item.get_attribute("data-id"):
                    classes = item.get_attribute("class") or ""
                    if testid.startswith("conv-msg-") or "xscbp6u" in classes:
                        row_el = item
                    else:
                        row_el = find(item, S["message_row"]) or item
                else:
                    continue

                mid = self._extractor.message_id(row_el)
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                rows.append(row_el)
            except StaleElementReferenceException:
                continue

        return rows

    def _ingest_row(
        self,
        panel,
        row_el,
        messages: Dict[str, Message],
        current_date: str,
        *,
        separator_seen_above: bool,
    ) -> Tuple[int, str]:
        """Resolve a row's date and store it if new. Returns (added, current_date)."""
        msg_date = self._resolve_date(
            panel,
            row_el,
            current_date,
            separator_seen_above=separator_seen_above,
        )

        mid = self._extractor.message_id(row_el)
        if mid and mid in messages:
            return 0, (msg_date or current_date)
        if mid and self.writer is not None and self.writer.has(mid):
            # Already persisted by this run or a prior one — skip but keep the
            # date so the scroll loop can track where we are.
            return 0, (msg_date or current_date)

        if not msg_date:
            return 0, current_date

        # Date-window filter: ignore rows newer than --to-date (still scrolling
        # down into the window) or older than --from-date (past the window).
        if not self._date_in_window(msg_date):
            return 0, msg_date

        msg = self._extractor.parse(row_el, msg_date)
        if not msg:
            return 0, current_date

        existing = messages.get(msg.id)
        added = 0
        if existing is None:
            messages[msg.id] = msg
            added = 1
            if self.writer is not None:
                self.writer.add(msg)
        elif existing.date != msg.date:
            messages[msg.id] = msg
        return added, msg_date

    def _date_in_window(self, msg_date: str) -> bool:
        """Whether a normalized dd/mm/yyyy date falls within the requested window."""
        if self.from_date is None and self.to_date is None:
            return True
        d = parse_chat_date(msg_date)
        if d is None:
            # Unparseable date — keep it rather than silently dropping data.
            return True
        if self.to_date is not None and d > self.to_date:
            return False
        if self.from_date is not None and d < self.from_date:
            return False
        return True

    def _below_from_date(self, msg_date: str) -> bool:
        """Whether a date is strictly older than the requested --from-date."""
        if self.from_date is None:
            return False
        d = parse_chat_date(msg_date)
        return d is not None and d < self.from_date

    def harvest_visible(
        self,
        panel,
        messages: Dict[str, Message],
        current_date: str,
    ) -> Tuple[int, str]:
        added = 0
        items = self._visible_chat_items(panel)
        items.sort(key=lambda item: item.get("top", 0))

        date_items = [
            i for i in items
            if i.get("kind") == "date"
            and self._normalize_date((i.get("text") or "").strip())
        ]
        if date_items and not self._dates_logged:
            labels = ", ".join(
                self._normalize_date((i.get("text") or "").strip())
                for i in date_items[:5]
            )
            log(f"  ✓  Date separators in view: {labels}")
            self._dates_logged = True

        has_message_items = any(item.get("kind") == "message" for item in items)

        if has_message_items:
            separator_top: float | None = None
            for item in items:
                try:
                    kind = item.get("kind")
                    if kind == "date":
                        raw = (item.get("text") or "").strip()
                        normalized = self._normalize_date(raw)
                        if normalized:
                            current_date = normalized
                            separator_top = item.get("top", 0)
                        continue

                    if kind != "message":
                        continue

                    row_el = item.get("el")
                    if not row_el:
                        continue

                    seen_below_separator = (
                        separator_top is not None
                        and item.get("top", 0) > separator_top + 1
                    )
                    added_delta, current_date = self._ingest_row(
                        panel,
                        row_el,
                        messages,
                        current_date,
                        separator_seen_above=seen_below_separator,
                    )
                    added += added_delta

                except StaleElementReferenceException:
                    continue
            return added, current_date

        separator_top = None
        for item in self._panel_items(panel):
            try:
                if (item.get_attribute("data-testid") or "") == "msg-dateseparator":
                    normalized = self._normalize_date(item.text.strip())
                    if normalized:
                        current_date = normalized
                        separator_top = 0.0
            except StaleElementReferenceException:
                continue

        for row_el in self._ordered_panel_rows(panel):
            try:
                added_delta, current_date = self._ingest_row(
                    panel,
                    row_el,
                    messages,
                    current_date,
                    separator_seen_above=separator_top is not None,
                )
                added += added_delta
            except StaleElementReferenceException:
                continue

        return added, current_date

    def _sort_messages(self, messages: Dict[str, Message]) -> List[Message]:
        return sort_messages(messages.values())

    @staticmethod
    def _fill_grouped_senders(ordered: List[Message]) -> None:
        fill_grouped_senders(ordered)

    def scrape(self) -> List[Message]:
        panel = WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, S["panel"]))
        )
        scroller = PanelScroller(self.driver, panel)

        try:
            self.driver.execute_script(
                "delete arguments[0].dataset.waReversed;", panel
            )
        except Exception:
            pass

        if self.scroll_to_chat_start:
            log("↑  Harvesting upward to the start of the chat (phone sync enabled) …")
        else:
            log("↑  Harvesting from newest messages upward …")
        jitter(0.8, 1.4)

        messages: Dict[str, Message] = {}
        current_date = self._ensure_date_context(panel, scroller, "")

        # Capture the initial (newest) viewport now, before the loop's first
        # "load older messages from phone" click can scroll the panel away from
        # the bottom of the chat (otherwise the newest messages are never seen).
        _, current_date = self.harvest_visible(panel, messages, current_date)

        no_new_streak = 0
        stall_streak = 0
        scroll_stuck_streak = 0
        prev_oldest_id = ""
        anchor_id: str | None = None
        prev_message_count = 0
        stagnant_steps = 0
        below_from_streak = 0
        sync_retry_streak = 0
        load_stall_streak = 0
        stale_reacquire_streak = 0

        try:
            for step_n in range(1, self.max_top_scrolls + 1):
                scroller.wait_no_spinner()

                if self._try_load_older_from_phone(panel, scroller):
                    no_new_streak = 0
                    stall_streak = 0
                    scroll_stuck_streak = 0
                    prev_oldest_id = ""
                    stagnant_steps = 0

                current_date = self._ensure_date_context(
                    panel, scroller, current_date
                )

                try:
                    added, current_date = self.harvest_visible(
                        panel, messages, current_date
                    )
                except StaleElementReferenceException:
                    panel = self.driver.find_element(By.CSS_SELECTOR, S["panel"])
                    scroller = PanelScroller(self.driver, panel)
                    added, current_date = self.harvest_visible(
                        panel, messages, current_date
                    )

                if added:
                    no_new_streak = 0
                else:
                    no_new_streak += 1

                if len(messages) == prev_message_count:
                    stagnant_steps += 1
                else:
                    stagnant_steps = 0
                prev_message_count = len(messages)

                if self.checkpoint_interval and step_n % self.checkpoint_interval == 0:
                    if self.writer is not None:
                        self.writer.checkpoint()

                metrics = scroller.metrics()

                # Empty metrics mean the panel element went stale (WhatsApp
                # re-rendered the message list). Re-acquire it and retry the
                # step, otherwise every JS call silently no-ops forever.
                if not metrics:
                    stale_reacquire_streak += 1
                    if stale_reacquire_streak >= 12:
                        log(
                            "\n⚠  Lost the conversation panel (DOM kept "
                            f"re-rendering).  {len(messages)} messages captured. "
                            "Re-run to resume."
                        )
                        break
                    try:
                        panel, scroller = self._reacquire_panel()
                    except Exception:
                        pass
                    jitter(0.5, 1.0)
                    continue
                else:
                    stale_reacquire_streak = 0

                visible_n = metrics.get("visibleCount", 0)
                oldest_id = metrics.get("oldestId") or ""
                anchor_id = scroller.topmost_anchor_id(messages, metrics)

                if step_n == 1 or step_n % 15 == 0 or added:
                    anchor_hint = f", anchor={anchor_id[:12]}…" if anchor_id else ""
                    log(
                        f"  ↑ step {step_n}: {len(messages)} total"
                        f" (+{added} new), visible={visible_n}{anchor_hint}"
                    )

                # Stop once we have scrolled past the requested --from-date.
                if self._below_from_date(current_date):
                    below_from_streak += 1
                    if below_from_streak >= 3:
                        log(
                            f"\n✓  Reached start of requested date range "
                            f"({self.from_date:%d/%m/%Y}).  "
                            f"{len(messages)} messages captured this run."
                        )
                        break
                else:
                    below_from_streak = 0

                prompt_kind = (
                    scroller.older_messages_prompt_kind()
                    if self.scroll_to_chat_start
                    else ""
                )
                older_prompt = bool(prompt_kind)

                # A persistent "Couldn't get older messages … try again" error
                # that never yields new messages means the phone is unreachable;
                # bail out cleanly instead of retrying forever.
                if prompt_kind == "retry" and added == 0:
                    sync_retry_streak += 1
                else:
                    sync_retry_streak = 0

                if sync_retry_streak >= 30:
                    self.harvest_visible(panel, messages, current_date)
                    log(
                        "\n⚠  Phone sync kept failing ('Couldn't get older "
                        f"messages').  {len(messages)} messages captured.  "
                        "Make sure WhatsApp is open and online on your phone, "
                        "then re-run to resume from where this stopped."
                    )
                    break

                # At the local-history boundary the normal "load older messages
                # from phone" prompt can persist while clicking it no longer
                # fetches anything (it only fires on a real scroll gesture).
                # Detect that stall and nudge the panel to retrigger the loader.
                if prompt_kind == "load" and added == 0:
                    load_stall_streak += 1
                else:
                    load_stall_streak = 0

                if load_stall_streak and load_stall_streak % 3 == 0:
                    self._nudge_to_trigger_phone_load(panel, scroller)
                    current_date = self._ensure_date_context(
                        panel, scroller, current_date
                    )
                    nudged_added, current_date = self.harvest_visible(
                        panel, messages, current_date
                    )

                    if nudged_added:
                        added += nudged_added
                        load_stall_streak = 0
                        no_new_streak = 0

                if load_stall_streak >= 30:
                    self.harvest_visible(panel, messages, current_date)
                    log(
                        f"\n⚠  WhatsApp will not load messages older than "
                        f"{current_date} into this linked device "
                        f"('Use WhatsApp on your phone to see older messages').  "
                        f"{len(messages)} messages captured — this is the limit "
                        "of the history WhatsApp Web syncs from your phone."
                    )
                    break

                if PanelScroller.reached_history_top(
                    metrics,
                    stall_streak=stall_streak,
                    scroll_stuck_streak=scroll_stuck_streak,
                    no_new_streak=no_new_streak,
                    prev_oldest_id=prev_oldest_id,
                    has_older_messages_prompt=older_prompt,
                ):
                    current_date = self._ensure_date_context(
                        panel, scroller, current_date
                    )
                    self.harvest_visible(panel, messages, current_date)
                    log(
                        f"\n✓  Reached the top of history.  "
                        f"{len(messages)} messages captured."
                    )
                    break

                scroll_result = scroller.scroll_up_from_anchor(anchor_id)
                scrolled = scroll_result.get("scrolled") or 0
                if scroll_result.get("stuck") or scrolled > 1:
                    scroll_stuck_streak += 1
                    scroller.scroll_fallback("up")
                else:
                    scroll_stuck_streak = 0

                if stagnant_steps >= 12 and scroll_stuck_streak < 3:
                    scroller.scroll_chunk(-(140 + (stagnant_steps % 5) * 24))

                metrics = scroller.metrics()
                oldest_id = metrics.get("oldestId") or oldest_id
                if oldest_id and oldest_id == prev_oldest_id:
                    stall_streak += 1
                else:
                    stall_streak = 0
                prev_oldest_id = oldest_id

                jitter(self.scroll_pause * 0.35, self.scroll_pause * 0.75)
            else:
                current_date = self._ensure_date_context(
                    panel, scroller, current_date
                )
                self.harvest_visible(panel, messages, current_date)
                log(
                    f"\n⚠  Stopped after {self.max_top_scrolls} upward scroll "
                    f"steps.  {len(messages)} messages captured."
                )
        finally:
            # Always flush captured messages — on clean finish, on a break, or
            # on an exception / KeyboardInterrupt — so no data is ever lost.
            if self.writer is not None:
                self.writer.close()

        ordered = self._sort_messages(messages)
        self._fill_grouped_senders(ordered)
        return ordered
