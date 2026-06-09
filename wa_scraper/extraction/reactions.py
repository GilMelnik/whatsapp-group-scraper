"""Reaction parsing from inline pills and the reactions drawer."""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional

from selenium.common.exceptions import (
    ElementNotInteractableException,
    StaleElementReferenceException,
    TimeoutException, NoSuchElementException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from wa_scraper.js.reactions import (
    _JS_COLLECT_REACTORS,
    _JS_COLLECT_REACTORS_GROUPED,
    _JS_FIND_REACTIONS_OVERLAY,
    _JS_HAS_REACTION_PARTICIPANTS,
    _JS_PARSE_REACTION_TABS,
    _JS_REACTIONS,
)
from wa_scraper.models import Reaction
from wa_scraper.selectors import S
from wa_scraper.utils import (
    click_element,
    emoji_matches,
    find,
    find_all,
    jitter,
    normalize_emoji,
)


class ReactionExtractor:
    """Extracts emoji reactions and optional reactor names from message rows."""

    def __init__(self, driver) -> None:
        self.driver = driver

    def _wait_for_dialog(self, timeout: float = 8.0):
        deadline = time.time() + timeout
        last_err: Optional[Exception] = None
        while time.time() < deadline:
            try:
                overlay = self.driver.execute_script(
                    _JS_FIND_REACTIONS_OVERLAY, None
                )
                if overlay:
                    return overlay
            except StaleElementReferenceException as exc:
                last_err = exc

            for drawer in self.driver.find_elements(
                By.CSS_SELECTOR, '[data-testid="drawer-right"]'
            ):
                try:
                    if not drawer.is_displayed():
                        continue
                    dialog = find(drawer, S["rxn_dialog"]) or drawer
                    if dialog.is_displayed():
                        return dialog
                except StaleElementReferenceException as exc:
                    last_err = exc

            for el in self.driver.find_elements(By.CSS_SELECTOR, S["rxn_dialog"]):
                try:
                    if el.is_displayed():
                        return el
                except StaleElementReferenceException as exc:
                    last_err = exc
            time.sleep(0.15)
        raise TimeoutException("Reaction dialog did not appear") from last_err

    def _wait_for_participants(self, dialog, timeout: float = 4.0) -> None:
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script(_JS_HAS_REACTION_PARTICIPANTS, dialog)
            )
        except TimeoutException:
            pass

    def parse_basic(self, row_el) -> List[Reaction]:
        try:
            raw = self.driver.execute_script(_JS_REACTIONS, row_el) or []
            return [
                Reaction(emoji=item["emoji"], count=int(item.get("count", 1)), senders=[])
                for item in raw
                if item.get("emoji")
            ]
        except Exception:
            pass

        results: List[Reaction] = []
        for container in find_all(row_el, S["rxn_container"]):
            try:
                spans = container.find_elements(By.TAG_NAME, "span")
                emoji = ""
                count = 1
                for sp in spans:
                    t = sp.text.strip()
                    if not t:
                        continue
                    if t.isdigit():
                        count = int(t)
                    else:
                        if emoji:
                            results.append(
                                Reaction(emoji=emoji, count=count, senders=[])
                            )
                        emoji = t
                        count = 1
                if emoji:
                    results.append(Reaction(emoji=emoji, count=count, senders=[]))
            except StaleElementReferenceException:
                pass
        return results

    def _collect_senders(self, dialog, emoji: str = "") -> List[str]:
        try:
            names = (
                self.driver.execute_script(_JS_COLLECT_REACTORS, dialog, emoji) or []
            )
            cleaned = [str(n).strip() for n in names if str(n).strip()]
            if cleaned:
                return cleaned
        except Exception:
            pass

        senders: List[str] = []
        seen: set = set()
        try:
            root = self.driver.execute_script(_JS_FIND_REACTIONS_OVERLAY, dialog)
        except Exception:
            root = dialog
        if not root:
            root = dialog

        scroll_el = (
            find(root, S["rxn_scroll"])
            or find(root, '[data-testid="reactions-list"]')
            or find(root, '[role="list"]')
            or find(root, S["rxn_dialog"])
            or root
        )

        for _ in range(50):
            for row in find_all(root, S["rxn_row"]):
                try:
                    role = (row.get_attribute("role") or "").lower()
                    testid = row.get_attribute("data-testid") or ""
                    if role == "tab" or testid == "reactions-tab":
                        continue
                except StaleElementReferenceException:
                    continue

                for name_el in find_all(row, S["rxn_name"]):
                    n = (name_el.get_attribute("title") or name_el.text or "").strip()
                    if n and n not in seen and not re.fullmatch(r"\d+", n):
                        seen.add(n)
                        senders.append(n)

                if not find_all(row, S["rxn_name"]):
                    for sp in find_all(row, 'span[title], span[dir="auto"]'):
                        n = (sp.get_attribute("title") or sp.text or "").strip()
                        if n and n not in seen and not re.fullmatch(r"\d+", n):
                            seen.add(n)
                            senders.append(n)

            try:
                prev = self.driver.execute_script(
                    "return arguments[0].scrollTop;", scroll_el
                )
                self.driver.execute_script(
                    "arguments[0].scrollTop += Math.max(120, arguments[0].clientHeight * 0.6);",
                    scroll_el,
                )
                cur = self.driver.execute_script(
                    "return arguments[0].scrollTop;", scroll_el
                )
                if cur == prev:
                    break
            except Exception:
                break
            jitter(0.1, 0.25)

        return senders

    def _find_container(self, row_el):
        scopes = [row_el]
        try:
            scopes.append(row_el.find_element(By.XPATH, ".."))
        except (NoSuchElementException, StaleElementReferenceException):
            pass
        try:
            scopes.append(
                row_el.find_element(
                    By.XPATH,
                    "./ancestor::div[contains(@class,'focusable-list-item')]",
                )
            )
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        selectors = (
            '[data-testid="reaction-bubble"]',
            '[data-testid="reactions-read-more-container"]',
            '[data-testid="addon-bubble-container"]',
            S["rxn_container"],
        )
        seen = set()
        for scope in scopes:
            if id(scope) in seen:
                continue
            seen.add(id(scope))
            for sel in selectors:
                for target in find_all(scope, sel):
                    try:
                        if target.is_displayed():
                            return target
                    except StaleElementReferenceException:
                        continue
        return None

    def _find_pill(self, row_el, emoji: str):
        scopes = [row_el]
        try:
            scopes.append(row_el.find_element(By.XPATH, ".."))
        except (NoSuchElementException, StaleElementReferenceException):
            pass
        try:
            root = row_el.find_element(
                By.XPATH,
                "./ancestor::div[contains(@class,'focusable-list-item')]",
            )
            scopes.append(root)
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        seen = set()
        for scope in scopes:
            if id(scope) in seen:
                continue
            seen.add(id(scope))
            for pill in find_all(scope, S["rxn_pill"]):
                try:
                    txt = pill.text.strip()
                    aria = (pill.get_attribute("aria-label") or "").strip()
                    if emoji_matches(emoji, txt) or emoji_matches(emoji, aria):
                        return pill
                except StaleElementReferenceException:
                    continue

            for container in find_all(scope, '[data-testid="addon-bubble-container"]'):
                for pill in find_all(container, '[data-testid="reaction-bubble"]'):
                    try:
                        aria = (pill.get_attribute("aria-label") or "").strip()
                        if emoji_matches(emoji, pill.text.strip()) or emoji_matches(
                            emoji, aria
                        ):
                            return pill
                    except StaleElementReferenceException:
                        continue

            for container in find_all(scope, S["rxn_container"]):
                for pill in find_all(container, '[role="button"], button'):
                    try:
                        if emoji_matches(emoji, pill.text.strip()):
                            return pill
                    except StaleElementReferenceException:
                        continue
        return None

    def _find_click_target(self, row_el, emoji: str = ""):
        if emoji:
            pill = self._find_pill(row_el, emoji)
            if pill:
                return pill
        return self._find_container(row_el)

    def _dismiss_dialog(self) -> None:
        try:
            ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            jitter(0.15, 0.3)
        except Exception:
            pass

    def _click_tab(self, dialog, emoji: str) -> None:
        from selenium.common.exceptions import NoSuchElementException

        root = dialog
        try:
            drawer = dialog.find_element(
                By.XPATH,
                "./ancestor::*[@data-testid='drawer-right'][1]",
            )
            root = drawer
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        tabs = find_all(root, S["rxn_tab"])
        if not tabs:
            tabs = find_all(root, '[role="tab"]')
        if not tabs:
            return

        for tab in tabs:
            try:
                tab_text = (
                    tab.get_attribute("aria-label")
                    or tab.get_attribute("title")
                    or tab.text
                    or ""
                ).strip()
                if emoji_matches(emoji, tab_text):
                    click_element(self.driver, tab)
                    jitter(0.25, 0.45)
                    return
            except StaleElementReferenceException:
                continue

    def _collect_senders_for_emoji(self, row_el, emoji: str) -> List[str]:
        target = self._find_pill(row_el, emoji) or self._find_click_target(
            row_el, emoji
        )
        if not target:
            return []

        try:
            click_element(self.driver, target)
            jitter(0.35, 0.75)
            dialog = self._wait_for_dialog()
            self._click_tab(dialog, emoji)
            self._wait_for_participants(dialog)
            jitter(0.2, 0.4)
            senders = self._collect_senders(dialog, "")
            if not senders:
                senders = self._collect_senders(dialog, emoji)
            return senders
        except (
            TimeoutException,
            StaleElementReferenceException,
            ElementNotInteractableException,
        ):
            return []
        finally:
            self._dismiss_dialog()
            jitter(0.15, 0.3)

    def _parse_from_dialog(
        self, dialog, basic: List[Reaction]
    ) -> List[Reaction]:
        try:
            tabs_raw = self.driver.execute_script(_JS_PARSE_REACTION_TABS, dialog) or []
        except Exception:
            tabs_raw = []

        if tabs_raw:
            detailed: List[Reaction] = []
            for tab_info in tabs_raw:
                emoji = tab_info.get("emoji", "")
                if not emoji:
                    continue
                count = int(tab_info.get("count", 1))
                self._click_tab(dialog, emoji)
                self._wait_for_participants(dialog)
                jitter(0.2, 0.4)
                senders = self._collect_senders(dialog, "")
                if not senders:
                    senders = self._collect_senders(dialog, emoji)
                detailed.append(Reaction(emoji=emoji, count=count, senders=senders))
            return detailed

        try:
            grouped_raw = (
                self.driver.execute_script(_JS_COLLECT_REACTORS_GROUPED, dialog) or []
            )
            if grouped_raw:
                return [
                    Reaction(
                        emoji=item["emoji"],
                        count=int(
                            item.get("count", len(item.get("senders", [])))
                        ),
                        senders=[
                            str(s).strip()
                            for s in item.get("senders", [])
                            if str(s).strip()
                        ],
                    )
                    for item in grouped_raw
                    if item.get("emoji")
                ]
        except Exception:
            pass

        senders = self._collect_senders(dialog, "")
        if senders and len(basic) == 1:
            only = basic[0]
            return [Reaction(emoji=only.emoji, count=only.count, senders=senders)]
        return []

    @staticmethod
    def _merge_senders(
        basic: List[Reaction], detailed: List[Reaction]
    ) -> List[Reaction]:
        by_emoji: Dict[str, Reaction] = {}
        for rxn in detailed:
            key = normalize_emoji(rxn.emoji)
            if key and key not in by_emoji:
                by_emoji[key] = rxn

        merged: List[Reaction] = []
        for rxn in basic:
            key = normalize_emoji(rxn.emoji)
            detail = by_emoji.get(key)
            senders = detail.senders if detail and detail.senders else []
            count = rxn.count if rxn.count else (detail.count if detail else 1)
            merged.append(Reaction(emoji=rxn.emoji, count=count, senders=senders))
        return merged

    def parse_detail(self, row_el) -> List[Reaction]:
        basic = self.parse_basic(row_el)
        if not basic:
            return []

        detailed: List[Reaction] = []
        for rxn in basic:
            senders = self._collect_senders_for_emoji(row_el, rxn.emoji)
            detailed.append(
                Reaction(emoji=rxn.emoji, count=rxn.count, senders=senders)
            )

        if any(r.senders for r in detailed):
            return detailed

        target = self._find_click_target(row_el, "")
        if not target:
            return basic

        try:
            click_element(self.driver, target)
            jitter(0.45, 0.9)
            dialog = self._wait_for_dialog()
            self._wait_for_participants(dialog)
            batch = self._parse_from_dialog(dialog, basic)
            self._dismiss_dialog()
            if batch and any(r.senders for r in batch):
                return self._merge_senders(basic, batch)
        except (
            TimeoutException,
            StaleElementReferenceException,
            ElementNotInteractableException,
        ):
            self._dismiss_dialog()

        return basic
