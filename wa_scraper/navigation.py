"""Chat search and navigation on WhatsApp Web."""

from __future__ import annotations

import random
import re
import time
from typing import List

from selenium.common.exceptions import (
    ElementNotInteractableException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from wa_scraper.js.navigation import (
    _JS_CELL_CHAT_TITLE,
    _JS_READ_HEADER_TITLE,
    _JS_SEARCH_RESULT_CELLS,
)
from wa_scraper.selectors import (
    APP_READY_SELECTORS,
    COMPOSE_BOX_SELECTORS,
    CONV_HEADER_TITLE_SELECTORS,
    SEARCH_RESULTS_ROOT_SELECTORS,
    S,
)
from wa_scraper.utils import (
    find_search_input,
    jitter,
    log,
    wait_for_any_selector,
)

_HEADER_GARBAGE_RE = re.compile(
    r"click here for group info|\+\d{1,3}[\s\-]?\d|\d+\s+more$",
    re.IGNORECASE,
)


class ChatNavigator:
    """Opens a WhatsApp group chat by searching the sidebar."""

    def __init__(self, driver) -> None:
        self.driver = driver

    @staticmethod
    def _norm_chat_name(value: str) -> str:
        return re.sub(r"\s+", " ", (value or "").strip()).casefold()

    def _cell_chat_title(self, cell) -> str:
        try:
            title = self.driver.execute_script(_JS_CELL_CHAT_TITLE, cell)
            if title:
                return str(title).strip()
        except Exception:
            pass

        for sel in ("span[title]", S["cell_title"]):
            for el in cell.find_elements(By.CSS_SELECTOR, sel):
                attr = (el.get_attribute("title") or "").strip()
                if attr:
                    return attr
                text = (el.text or "").strip()
                if text:
                    return text
        return ""

    def _find_search_result_cells(self) -> List[object]:
        try:
            cells = self.driver.execute_script(
                _JS_SEARCH_RESULT_CELLS,
                SEARCH_RESULTS_ROOT_SELECTORS,
            )
            if cells:
                return list(cells)
        except Exception:
            pass

        root = None
        for sel in SEARCH_RESULTS_ROOT_SELECTORS:
            found = self.driver.find_elements(By.CSS_SELECTOR, sel)
            if found:
                root = found[0]
                break

        if root is None:
            return []

        cells = root.find_elements(By.CSS_SELECTOR, '[role="option"]')
        if cells:
            return cells

        return root.find_elements(By.CSS_SELECTOR, S["chat_cell"])

    @staticmethod
    def _looks_like_header_garbage(title: str) -> bool:
        t = (title or "").strip()
        if not t or t.startswith("wds-"):
            return True
        if _HEADER_GARBAGE_RE.search(t):
            return True
        if t.count(",") >= 3 or len(t) > 100:
            return True
        return False

    def _read_open_chat_title(self) -> str:
        try:
            title = self.driver.execute_script(_JS_READ_HEADER_TITLE)
            if title and not self._looks_like_header_garbage(str(title)):
                return str(title).strip()
        except Exception:
            pass

        for sel in CONV_HEADER_TITLE_SELECTORS:
            for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                title = (el.get_attribute("title") or el.text or "").strip()
                if title and not self._looks_like_header_garbage(title):
                    return title
        return ""

    def _compose_box_visible(self) -> bool:
        for sel in COMPOSE_BOX_SELECTORS:
            for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    if el.is_displayed():
                        return True
                except StaleElementReferenceException:
                    continue
        return False

    def _chat_title_matches(self, wanted: str, opened: str) -> bool:
        w = self._norm_chat_name(wanted)
        o = self._norm_chat_name(opened)
        if not w or not o:
            return False
        return w == o or w in o or o in w

    def _wait_for_chat_open(self, name: str, timeout: float = 20) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._compose_box_visible():
                opened = self._read_open_chat_title()
                if opened and not self._chat_title_matches(name, opened):
                    log(f"  ⚠  Header shows {opened!r}, wanted {name!r}")
                    return False
                return True

            opened = self._read_open_chat_title()
            if opened and self._chat_title_matches(name, opened):
                jitter(0.4, 0.8)
                if self._compose_box_visible():
                    return True

            time.sleep(0.25)
        return False

    def _click_search_result(self, cell, title: str) -> None:
        target = cell
        try:
            row = self.driver.execute_script(
                'return arguments[0].closest(\'[role="option"]\') || arguments[0];',
                cell,
            )
            if row:
                target = row
        except Exception:
            pass

        try:
            titled = self.driver.execute_script(
                """
                var root = arguments[0];
                var needle = arguments[1].toLowerCase().trim();
                var spans = root.querySelectorAll(
                    '[data-testid="cell-frame-title"] span, span[title]'
                );
                for (var i = 0; i < spans.length; i++) {
                    var sp = spans[i];
                    var t = (sp.getAttribute('title') || sp.textContent || '')
                        .replace(/\\s+/g, ' ').trim().toLowerCase();
                    if (t === needle) return sp;
                }
                return null;
                """,
                target,
                title,
            )
            if titled:
                target = titled
        except Exception:
            pass

        self.driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", target
        )
        jitter(0.25, 0.45)

        clicked = False
        for attempt in (
            lambda: target.click(),
            lambda: ActionChains(self.driver).move_to_element(target).click().perform(),
            lambda: self.driver.execute_script("arguments[0].click();", target),
        ):
            try:
                attempt()
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            self.driver.execute_script(
                """
                var el = arguments[0];
                ['mousedown', 'mouseup', 'click'].forEach(function(type) {
                    el.dispatchEvent(new MouseEvent(type, {
                        bubbles: true, cancelable: true, view: window
                    }));
                });
                """,
                target,
            )
        jitter(0.8, 1.4)

    def _open_first_exact_search_result(self, name: str) -> bool:
        needle = self._norm_chat_name(name)
        for cell in self._find_search_result_cells():
            title = self._cell_chat_title(cell)
            if self._norm_chat_name(title) != needle:
                continue

            log(f"Opening chat: {title!r}")
            self._click_search_result(cell, title)
            if self._wait_for_chat_open(name):
                log("  Chat open ✓")
                return True

            log(f"  ⚠  Chat did not open for {name!r}")
            return False

        return False

    def open(self, name: str) -> bool:
        """Type the group name into the search box and open the first exact match."""
        wait_for_any_selector(self.driver, APP_READY_SELECTORS, timeout=30)
        box = find_search_input(self.driver)
        if box is None:
            log("  ✗  Could not find the WhatsApp search input.")
            return False

        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'}); arguments[0].focus();",
                box,
            )
            box.click()
        except ElementNotInteractableException:
            self.driver.execute_script("arguments[0].focus();", box)
        jitter(0.4, 0.8)

        try:
            box.clear()
        except Exception:
            box.send_keys(Keys.CONTROL, "a")
            box.send_keys(Keys.DELETE)

        log(f"Searching for {name!r} …")
        for ch in name:
            box.send_keys(ch)
            time.sleep(random.uniform(0.04, 0.16))
        jitter(1.0, 1.8)

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, S["chat_cell"]))
            )
        except TimeoutException:
            log(f"  ✗  No chat found for {name!r}.")
            return False

        if self._open_first_exact_search_result(name):
            return True

        log(f"  ✗  Could not open {name!r} from search results.")
        return False

