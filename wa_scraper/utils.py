"""Shared utility helpers for Selenium interaction and timing."""

from __future__ import annotations

import random
import re
import time
from typing import List, Optional

from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from wa_scraper.selectors import SEARCH_INPUT_SELECTORS


def log(msg: str) -> None:
    """Print immediately so progress is visible when stdout is piped/buffered."""
    print(msg, flush=True)


def jitter(lo: float = 0.3, hi: float = 1.0) -> None:
    """Random sleep to break up robotic timing patterns."""
    time.sleep(random.uniform(lo, hi))


def wait_for_any_selector(
    driver,
    selectors: List[str],
    timeout: float,
    condition=EC.presence_of_element_located,
):
    """Return the first element matched by any selector within timeout."""
    deadline = time.time() + timeout
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for sel in selectors:
            try:
                return WebDriverWait(driver, 0.5).until(
                    condition((By.CSS_SELECTOR, sel))
                )
            except TimeoutException as exc:
                last_err = exc
        time.sleep(0.25)
    raise TimeoutException(
        f"Timed out waiting for any of: {', '.join(selectors)}"
    ) from last_err


def find(el, css: str):
    """Return the first matching element, or None."""
    try:
        return el.find_element(By.CSS_SELECTOR, css)
    except (NoSuchElementException, StaleElementReferenceException):
        return None


def find_all(el, css: str) -> list:
    try:
        return el.find_elements(By.CSS_SELECTOR, css)
    except (NoSuchElementException, StaleElementReferenceException):
        return []


def find_search_input(driver) -> Optional[object]:
    """Locate the real search input (WhatsApp moved it inside a container div)."""
    for sel in SEARCH_INPUT_SELECTORS:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if not els:
            continue
        el = els[0]
        if el.tag_name.lower() == "input":
            return el
        inner = find(el, 'input[type="text"]') or find(el, "input")
        if inner:
            return inner
        if el.get_attribute("contenteditable") == "true":
            return el
    return None


def scroll_by(driver, el, px: int, step: int = 120) -> None:
    """
    Scroll a DOM element by `px` pixels in small, randomised increments
    that look like a real human scrolling with a mouse wheel.
    """
    sign = 1 if px > 0 else -1
    remaining = abs(px)
    while remaining > 0:
        chunk = min(remaining, random.randint(step // 2, int(step * 1.6)))
        driver.execute_script(
            "arguments[0].scrollTop += arguments[1];", el, sign * chunk
        )
        remaining -= chunk
        time.sleep(random.uniform(0.012, 0.055))


def normalize_emoji(value: str) -> str:
    """Strip variation selectors so ❤ and ❤️ compare equal."""
    if not value:
        return ""
    return re.sub(r"[\uFE0E\uFE0F\u200D]", "", value)


def emoji_matches(left: str, right: str) -> bool:
    """Return True when two strings refer to the same reaction emoji."""
    left_n = normalize_emoji(left)
    right_n = normalize_emoji(right)
    if not left_n or not right_n:
        return False
    return left_n in right_n or right_n in left_n


def click_element(driver, el) -> None:
    """Click an element with human-like pointer interaction."""
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center', inline:'nearest'});",
        el,
    )
    jitter(0.15, 0.35)
    try:
        ActionChains(driver).move_to_element(el).click().perform()
    except (ElementNotInteractableException, StaleElementReferenceException):
        driver.execute_script("arguments[0].click();", el)
