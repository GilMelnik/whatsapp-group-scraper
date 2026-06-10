"""Reaction parsing from the reactions drawer (reactor users only)."""

from __future__ import annotations

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
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from wa_scraper.contact import Contact, dedupe_contacts, parse_contact
from wa_scraper.js.reactions import (
    _JS_COLLECT_REACTORS_GROUPED,
    _JS_FIND_REACTIONS_OVERLAY,
    _JS_FIRST_EMOJI,
    _JS_HAS_REACTION_PARTICIPANTS,
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
    """Extracts emoji reactions by scraping every reactor from the drawer."""

    def __init__(self, driver) -> None:
        self.driver = driver

    def parse(self, row_el) -> List[Reaction]:
        if not self._has_reactions(row_el):
            return []

        target = self._find_click_target(row_el)
        if not target:
            return []

        try:
            click_element(self.driver, target)
            jitter(0.45, 0.9)
            dialog = self._wait_for_dialog()
            self._wait_for_participants(dialog)
            jitter(0.2, 0.4)
            reactions = self._collect_from_dialog(dialog)
            self._dismiss_dialog()
            return reactions
        except (
            TimeoutException,
            StaleElementReferenceException,
            ElementNotInteractableException,
        ):
            self._dismiss_dialog()
            return []

    def _has_reactions(self, row_el) -> bool:
        return self._find_container(row_el) is not None

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

            dialog_candidates = self.driver.find_elements(
                By.CSS_SELECTOR,
                (
                    '[data-testid="reaction-count-header"],'
                    '[data-testid="reactions-list"],'
                    '[data-testid="drawer-right"],'
                    '[role="list"]'
                ),
            )
            for el in reversed(dialog_candidates):
                try:
                    if not el.is_displayed():
                        continue
                    overlay = self.driver.execute_script(
                        _JS_FIND_REACTIONS_OVERLAY, el
                    )
                    if overlay and overlay.is_displayed():
                        return overlay
                except StaleElementReferenceException as exc:
                    last_err = exc

            detail_cells = self.driver.find_elements(
                By.CSS_SELECTOR,
                (
                    '[data-testid="reactions-details-cell-not-me"],'
                    '[data-testid="reactions-details-cell-me"],'
                    '[role="listitem"][aria-label*="reacted"]'
                ),
            )
            for el in reversed(detail_cells):
                try:
                    overlay = self.driver.execute_script(
                        _JS_FIND_REACTIONS_OVERLAY, el
                    )
                    if overlay and overlay.is_displayed():
                        return overlay
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

    def _find_click_target(self, row_el):
        return self._find_container(row_el)

    def _dismiss_dialog(self) -> None:
        try:
            ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            jitter(0.15, 0.3)
        except Exception:
            pass

    def _first_emoji(self, text: str) -> str:
        try:
            return (
                self.driver.execute_script(_JS_FIRST_EMOJI, text or "") or ""
            ).strip()
        except Exception:
            return ""

    def _tab_emojis(self, dialog) -> List[str]:
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

        emojis: List[str] = []
        seen: set[str] = set()
        for tab in tabs:
            try:
                txt = (
                    tab.get_attribute("aria-label")
                    or tab.get_attribute("title")
                    or tab.text
                    or ""
                ).strip()
                if re.match(
                    r"^(All|הכל|כל(?:\s+\d+)?|\d+\s+(?:reactions|תגובות))",
                    txt,
                    re.I,
                ):
                    continue

                emoji = self._first_emoji(txt)
                if not emoji:
                    img = find(tab, "img[alt]")
                    if img:
                        emoji = self._first_emoji(img.get_attribute("alt") or "")
                if not emoji:
                    continue

                key = normalize_emoji(emoji)
                if key in seen:
                    continue
                seen.add(key)
                emojis.append(emoji)
            except StaleElementReferenceException:
                continue
        return emojis

    def _click_tab(self, dialog, emoji: str) -> None:
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

    def _grouped_to_reactions(self, grouped_raw: List[dict]) -> List[Reaction]:
        reactions: List[Reaction] = []
        for item in grouped_raw:
            emoji = item.get("emoji")
            if not emoji:
                continue
            senders = dedupe_contacts(
                [
                    r
                    for r in (parse_contact(s) for s in item.get("senders", []))
                    if r is not None
                ]
            )
            if not senders:
                continue
            reactions.append(
                Reaction(emoji=emoji, count=len(senders), senders=senders)
            )
        return reactions

    def _collect_grouped_from_dialog(self, dialog) -> List[Reaction]:
        try:
            grouped_raw = (
                self.driver.execute_script(_JS_COLLECT_REACTORS_GROUPED, dialog)
                or []
            )
            if grouped_raw:
                return self._grouped_to_reactions(grouped_raw)
        except Exception:
            pass
        return []

    def _collect_from_dialog(self, dialog) -> List[Reaction]:
        grouped = self._collect_grouped_from_dialog(dialog)
        if grouped:
            return grouped

        detailed: List[Reaction] = []
        seen: set[str] = set()
        for emoji in self._tab_emojis(dialog):
            self._click_tab(dialog, emoji)
            self._wait_for_participants(dialog)
            jitter(0.2, 0.4)

            senders: List[Contact] = []
            resolved_emoji = emoji
            for rxn in self._collect_grouped_from_dialog(dialog):
                if emoji_matches(emoji, rxn.emoji):
                    senders = rxn.senders
                    resolved_emoji = rxn.emoji
                    break
            if not senders:
                tab_grouped = self._collect_grouped_from_dialog(dialog)
                if len(tab_grouped) == 1 and tab_grouped[0].senders:
                    resolved_emoji = tab_grouped[0].emoji or emoji
                    senders = tab_grouped[0].senders

            if not senders:
                continue

            key = normalize_emoji(resolved_emoji)
            if key in seen:
                continue
            seen.add(key)
            detailed.append(
                Reaction(
                    emoji=resolved_emoji,
                    count=len(senders),
                    senders=senders,
                )
            )
        return detailed
