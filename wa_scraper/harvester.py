"""Harvest messages from the visible chat panel viewport."""

from __future__ import annotations

from datetime import date
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
        max_down_passes: int = 10,
    ) -> None:
        self.driver = driver
        self.scroll_pause = scroll_pause
        self.max_top_scrolls = max_top_scrolls
        self.max_down_passes = max_down_passes
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

    def scrape(self) -> List[Message]:
        panel = WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, S["panel"]))
        )
        scroller = PanelScroller(self.driver, panel)

        log("↑  Rewinding to the start of history …")
        rewind_metrics = scroller.scroll_to_top(
            self.scroll_pause, self.max_top_scrolls
        )
        rewind_ok = PanelScroller.rewind_near_top(rewind_metrics)
        if not rewind_ok:
            log(
                "  ⚠  Rewind did not reach the top — will keep scrolling up"
                " while harvesting."
            )

        log("↓  Reading messages and collecting reactors …")
        jitter(0.8, 1.4)

        messages: Dict[str, Message] = {}
        current_date = self._discover_date(panel)
        no_new_streak = 0
        step_n = 0
        stall_streak = 0
        prev_message_count = 0

        while step_n < self.max_down_passes:
            step_n += 1
            scroller.wait_no_spinner()
            added = 0

            if not rewind_ok:
                up_metrics = scroller.metrics()
                for _ in range(8):
                    scroller.wait_no_spinner()
                    up_result = scroller.scroll("up")
                    if up_result.get("stuck"):
                        scroller.scroll_fallback("up")
                    up_metrics = scroller.metrics()
                    if PanelScroller.rewind_near_top(up_metrics):
                        rewind_ok = True
                        break
                    jitter(self.scroll_pause * 0.1, self.scroll_pause * 0.2)
                if step_n == 1 or step_n % 10 == 0 or rewind_ok:
                    log(
                        "  ↑  loading history"
                        f" (logicalTop={up_metrics.get('scrollTop', 0)}) …"
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

            metrics = scroller.metrics()
            scroll_top = metrics.get("scrollTop", 0)
            visible_n = metrics.get("visibleCount", 0)
            at_bottom = metrics.get("atBottom", False)

            if step_n == 1 or step_n % 15 == 0 or added:
                log(
                    f"  ↓ step {step_n}: {len(messages)} total"
                    f" (+{added} new), visible={visible_n}, scrollTop={scroll_top}"
                )

            if at_bottom and no_new_streak >= 5 and rewind_ok:
                self.harvest_visible(panel, messages, current_date)
                log(f"\n✓  End of chat reached.  {len(messages)} messages captured.")
                break

            if not rewind_ok:
                no_new_streak = 0
                stall_streak = 0
                continue

            scroll_result = scroller.scroll("down")
            if scroll_result.get("stuck"):
                stall_streak += 1
                scroller.scroll_fallback("down")
            else:
                stall_streak = 0

            if stall_streak >= 8 and no_new_streak >= 5:
                log(
                    f"\n✓  Scroll stalled near end.  {len(messages)} messages captured."
                )
                break

            if (
                no_new_streak >= 12
                and at_bottom
                and len(messages) == prev_message_count
                and step_n > 6
            ):
                log(
                    f"\n✓  No new messages near bottom.  "
                    f"{len(messages)} messages captured."
                )
                break

            prev_message_count = len(messages)
            jitter(self.scroll_pause * 0.35, self.scroll_pause * 0.75)

        else:
            log(
                f"\n⚠  Stopped after {self.max_down_passes} down-scroll steps.  "
                f"{len(messages)} messages captured."
            )

        added, current_date = self.harvest_visible(panel, messages, current_date)
        if added:
            log(f"  Final viewport harvest: +{added} messages")

        return list(messages.values())
