"""Chat message panel scrolling and metrics."""

from __future__ import annotations

import random
import time

from selenium.common.exceptions import ElementNotInteractableException,StaleElementReferenceException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

from wa_scraper.js.scroll import _JS_PANEL_METRICS, _JS_PANEL_SCROLL, _JS_SCROLL_HELPERS
from wa_scraper.selectors import S
from wa_scraper.utils import find, jitter, log, scroll_by


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

    def metrics(self) -> dict:
        try:
            result = self.driver.execute_script(_JS_PANEL_METRICS, self.panel)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        return {}

    def scroll(self, direction: str = "down", overlap: int = 28) -> dict:
        try:
            result = self.driver.execute_script(
                _JS_PANEL_SCROLL, self.panel, direction, overlap
            )
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        return {"scrolled": 0, "stuck": True, "atBottom": False, "atTop": False}

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
                var sign = px < 0 ? -1 : 1;
                var remaining = Math.abs(px);
                while (remaining > 0) {
                    var chunk = Math.min(remaining, 90 + Math.floor(Math.random() * 80));
                    for (var i = 0; i < targets.length; i++) {
                        targets[i].scrollTop += sign * chunk;
                        _waDispatchWheel(targets[i], sign * chunk);
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
        try:
            scroll_el = self.driver.execute_script(
                _JS_SCROLL_HELPERS + "return _waFindScrollEl(arguments[0]);",
                self.panel,
            )
            if scroll_el:
                try:
                    self.driver.execute_script(
                        "arguments[0].focus(); arguments[0].click();", scroll_el
                    )
                except Exception:
                    pass
                delta = -500 if direction == "up" else 500
                self.driver.execute_script(
                    _JS_SCROLL_HELPERS
                    + "var el=arguments[0], px=arguments[1];"
                    + "el.scrollTop += px; _waDispatchWheel(el, px);",
                    scroll_el,
                    delta,
                )
        except Exception:
            pass
        try:
            self.panel.click()
        except (ElementNotInteractableException, StaleElementReferenceException):
            pass
        try:
            key = Keys.PAGE_UP if direction == "up" else Keys.PAGE_DOWN
            for _ in range(2 if direction == "up" else 1):
                ActionChains(self.driver).send_keys(key).perform()
                time.sleep(0.05)
        except Exception:
            pass

    @staticmethod
    def rewind_near_top(metrics: dict) -> bool:
        logical = metrics.get("scrollTop", 0)
        max_logical = max(
            0,
            metrics.get("scrollHeight", 0) - metrics.get("clientHeight", 0),
        )
        if max_logical <= 0:
            return True
        return logical <= max(max_logical * 0.05, 24)

    def scroll_to_top(self, scroll_pause: float, max_steps: int) -> dict:
        stall_n = 0
        prev_oldest_id = ""
        prev_logical = -1
        log_every = 1 if max_steps <= 25 else 20

        for step in range(1, max_steps + 1):
            self.wait_no_spinner()
            metrics = self.metrics()
            top = metrics.get("scrollTop", 0)
            oldest_id = metrics.get("oldestId") or ""

            if step == 1 or step % log_every == 0:
                visible = metrics.get("visibleCount", 0)
                reversed_flag = metrics.get("reversed", False)
                raw_top = metrics.get("rawScrollTop", 0)
                log(
                    f"  ↑ step {step}: logicalTop={top}, raw={raw_top},"
                    f" visible={visible}"
                    f"{', reversed' if reversed_flag else ''}"
                )

            if self.rewind_near_top(metrics):
                jitter(0.8, 1.5)
                self.wait_no_spinner()
                metrics = self.metrics()
                if self.rewind_near_top(metrics):
                    log(f"  ↑ reached top ({step} steps)")
                    return metrics

            result = self.scroll("up")
            if result.get("stuck"):
                stall_n += 1
                self.scroll_chunk(-random.randint(450, 750))
                self.scroll_fallback("up")
                jitter(0.6, 1.2)
                self.wait_no_spinner()
                metrics = self.metrics()
                oldest_id = metrics.get("oldestId") or oldest_id
                top = metrics.get("scrollTop", top)
                if stall_n >= 5 and oldest_id == prev_oldest_id and top == prev_logical:
                    log(f"  ↑ top of loaded history (logicalTop={top})")
                    return metrics
            else:
                stall_n = 0

            if oldest_id and oldest_id != prev_oldest_id:
                prev_oldest_id = oldest_id
                stall_n = 0

            prev_logical = top
            jitter(scroll_pause * 0.15, scroll_pause * 0.35)

        return self.metrics()
