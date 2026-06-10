"""Harvest messages from the visible chat panel viewport."""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Tuple

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from wa_scraper.extraction.message import MessageExtractor
from wa_scraper.js.scroll import (
    _JS_CHAT_ITEMS,
    _JS_DATE_FOR_ROW,
    _JS_DISCOVER_DATE,
)
from wa_scraper.localization import is_date_label, normalize_date_impl
from wa_scraper.models import Message
from wa_scraper.scroll.panel import PanelScroller
from wa_scraper.selectors import CHAT_ITEM_SELECTOR, S
from wa_scraper.utils import find, jitter, log


class MessageHarvester:
    """Scrolls through a chat and collects all messages into a dict by id."""

    def __init__(
        self,
        driver,
        scroll_pause: float = 1.0,
        max_top_scrolls: int = 6000,
    ) -> None:
        self.driver = driver
        self.scroll_pause = scroll_pause
        self.max_top_scrolls = max_top_scrolls
        self._extractor = MessageExtractor(driver)
        self._date_fallback_warned = False
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

    def _discover_date(self, panel) -> str:
        try:
            raw = self.driver.execute_script(_JS_DISCOVER_DATE, panel, 8)
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

    def _resolve_date(self, panel, row_el, current_date: str) -> str:
        row_date = self._date_for_row(panel, row_el)
        if row_date:
            return row_date

        if current_date:
            return current_date

        discovered = self._discover_date(panel)
        if discovered:
            return discovered

        if not self._date_fallback_warned:
            log(
                "  ⚠  No in-chat date separator found yet;"
                " using today until one appears."
            )
            self._date_fallback_warned = True
        return date.today().strftime("%d/%m/%Y")

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
            for item in items:
                try:
                    kind = item.get("kind")
                    if kind == "date":
                        raw = (item.get("text") or "").strip()
                        normalized = self._normalize_date(raw)
                        if normalized:
                            current_date = normalized
                        continue

                    if kind != "message":
                        continue

                    row_el = item.get("el")
                    if not row_el:
                        continue

                    mid = self._extractor.message_id(row_el)
                    if mid and mid in messages:
                        msg_date = self._resolve_date(panel, row_el, current_date)
                        if msg_date:
                            current_date = msg_date
                        continue

                    msg_date = self._resolve_date(panel, row_el, current_date)
                    msg = self._extractor.parse(row_el, msg_date)
                    if not msg:
                        continue
                    existing = messages.get(msg.id)
                    if existing is None:
                        messages[msg.id] = msg
                        added += 1
                    elif existing.date != msg.date:
                        messages[msg.id] = msg
                    current_date = msg_date

                except StaleElementReferenceException:
                    continue
            return added, current_date

        for item in self._panel_items(panel):
            try:
                if (item.get_attribute("data-testid") or "") == "msg-dateseparator":
                    normalized = self._normalize_date(item.text.strip())
                    if normalized:
                        current_date = normalized
            except StaleElementReferenceException:
                continue

        for row_el in self._ordered_panel_rows(panel):
            try:
                mid = self._extractor.message_id(row_el)
                if mid and mid in messages:
                    msg_date = self._resolve_date(panel, row_el, current_date)
                    if msg_date:
                        current_date = msg_date
                    continue

                msg_date = self._resolve_date(panel, row_el, current_date)
                msg = self._extractor.parse(row_el, msg_date)
                if not msg:
                    continue
                existing = messages.get(msg.id)
                if existing is None:
                    messages[msg.id] = msg
                    added += 1
                elif existing.date != msg.date:
                    messages[msg.id] = msg
                current_date = msg_date
            except StaleElementReferenceException:
                continue

        return added, current_date

    def _sort_messages(self, messages: Dict[str, Message]) -> List[Message]:
        def sort_key(msg: Message):
            for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
                try:
                    ts = datetime.strptime(f"{msg.date} {msg.time}", fmt).timestamp()
                    return (0, ts, msg.id)
                except ValueError:
                    continue
            return (1, 0.0, msg.id)

        return sorted(messages.values(), key=sort_key)

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

        log("↑  Harvesting from newest messages upward …")
        jitter(0.8, 1.4)

        messages: Dict[str, Message] = {}
        current_date = self._discover_date(panel)
        no_new_streak = 0
        stall_streak = 0
        scroll_stuck_streak = 0
        prev_oldest_id = ""
        anchor_id: str | None = None
        prev_message_count = 0
        stagnant_steps = 0

        for step_n in range(1, self.max_top_scrolls + 1):
            scroller.wait_no_spinner()

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

            metrics = scroller.metrics()
            visible_n = metrics.get("visibleCount", 0)
            oldest_id = metrics.get("oldestId") or ""
            anchor_id = scroller.topmost_anchor_id(messages, metrics)

            if step_n == 1 or step_n % 15 == 0 or added:
                anchor_hint = f", anchor={anchor_id[:12]}…" if anchor_id else ""
                log(
                    f"  ↑ step {step_n}: {len(messages)} total"
                    f" (+{added} new), visible={visible_n}{anchor_hint}"
                )

            if PanelScroller.reached_history_top(
                metrics,
                stall_streak=stall_streak,
                scroll_stuck_streak=scroll_stuck_streak,
                no_new_streak=no_new_streak,
                prev_oldest_id=prev_oldest_id,
            ):
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
            self.harvest_visible(panel, messages, current_date)
            log(
                f"\n⚠  Stopped after {self.max_top_scrolls} upward scroll steps.  "
                f"{len(messages)} messages captured."
            )

        return self._sort_messages(messages)
