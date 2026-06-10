"""Message field extraction from WhatsApp Web DOM rows."""

from __future__ import annotations

import re
from typing import Optional

from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By

from wa_scraper.extraction.reactions import ReactionExtractor
from wa_scraper.js.message import (
    _JS_MSG_TEXT,
    _JS_PARSE_MESSAGE,
    _JS_QUOTE_PARSE,
    _JS_SENDER,
)
from wa_scraper.contact import Contact, parse_contact
from wa_scraper.models import Message, Quote
from wa_scraper.selectors import MEDIA_CHECKS, S
from wa_scraper.utils import find, find_all


class MessageExtractor:
    """Parses a single message row into a Message dataclass."""

    def __init__(self, driver) -> None:
        self.driver = driver
        self._reactions = ReactionExtractor(driver)

    def message_id(self, row_el) -> str:
        mid = row_el.get_attribute("data-id") or ""
        if mid:
            return mid
        for sel in ("div[data-id]", "[data-id]"):
            inner = find(row_el, sel)
            if inner:
                mid = inner.get_attribute("data-id") or ""
                if mid:
                    return mid
        try:
            cur = row_el
            for _ in range(10):
                parent = cur.find_element(By.XPATH, "..")
                mid = parent.get_attribute("data-id") or ""
                if mid:
                    return mid
                cur = parent
        except (NoSuchElementException, StaleElementReferenceException):
            pass
        return ""

    def reaction_scope(self, row_el):
        try:
            return row_el.find_element(
                By.XPATH,
                "./ancestor::div[contains(@class,'focusable-list-item')][1]",
            )
        except (NoSuchElementException, StaleElementReferenceException):
            return row_el

    def is_notification_row(self, row_el) -> bool:
        if find(
            row_el,
            (
                '[data-testid="msg-notification-container"], '
                '[data-testid="subtype-invite"], '
                '[data-testid="subtype-add"], '
                '[data-testid="system_message"]'
            ),
        ):
            return True
        container = find(row_el, '[data-testid="msg-container"]') or row_el
        try:
            if (container.get_attribute("data-testid") or "") == "msg-notification-container":
                return True
        except StaleElementReferenceException:
            return True
        return not find(container, S["msg_meta"]) and not find(
            row_el, f'{S["tail_out"]}, {S["tail_in"]}'
        )

    def is_outgoing(self, row_el) -> bool:
        mid = self.message_id(row_el)
        if mid.startswith("true_"):
            return True
        if find(row_el, S["tail_out"]):
            return True
        try:
            cls = row_el.get_attribute("class") or ""
            if "message-out" in cls.split():
                return True
        except StaleElementReferenceException:
            pass
        return bool(find(row_el, ".message-out"))

    def parse_dom(self, row_el) -> Optional[dict]:
        try:
            return self.driver.execute_script(_JS_PARSE_MESSAGE, row_el)
        except Exception:
            return None

    def find_quote_block(self, row_el):
        container = find(row_el, '[data-testid="msg-container"]') or row_el
        for sel in (S["quote_block"],):
            block = find(container, sel)
            if block is not None:
                return block
        return None

    def extract_text(self, row_el) -> str:
        dom = self.parse_dom(row_el)
        if dom and dom.get("text"):
            return str(dom["text"]).strip()
        try:
            result = self.driver.execute_script(_JS_MSG_TEXT, row_el)
            if result:
                return str(result).strip()
        except Exception:
            pass

        container = find(row_el, '[data-testid="msg-container"]') or row_el
        quote_block = self.find_quote_block(row_el)
        parts = []
        for sp in find_all(container, S["msg_text"]):
            try:
                if quote_block:
                    inside = self.driver.execute_script(
                        "return arguments[0] && arguments[0].contains(arguments[1]);",
                        quote_block,
                        sp,
                    )
                    if inside:
                        continue
            except Exception:
                pass
            t = sp.text.strip()
            if t:
                parts.append(t)
        return "\n".join(parts).strip()

    def extract_sender(self, row_el, is_out: bool) -> Contact:
        if is_out:
            return Contact(user_name="You", phone=None)
        dom = self.parse_dom(row_el)
        if dom and dom.get("sender"):
            parsed = parse_contact(dom["sender"])
            if parsed:
                return parsed
        try:
            result = self.driver.execute_script(_JS_SENDER, row_el)
            parsed = parse_contact(result)
            if parsed and (parsed.user_name or parsed.phone):
                return parsed
        except Exception:
            pass

        container = find(row_el, '[data-testid="msg-container"]') or row_el
        author_el = find(container, S["author"])
        if author_el:
            parsed = parse_contact(author_el.text.strip())
            if parsed:
                return parsed

        copyable = find(row_el, "[data-pre-plain-text]")
        if copyable:
            pre = copyable.get_attribute("data-pre-plain-text") or ""
            m = re.match(r"^\[[^\]]+\]\s*(.+?):\s*$", pre.strip())
            if m:
                parsed = parse_contact(m.group(1).strip())
                if parsed:
                    return parsed

        return Contact(user_name="", phone=None)

    def _parse_sender(self, raw) -> Contact:
        parsed = parse_contact(raw)
        return parsed or Contact(user_name="", phone=None)

    def parse_quote(self, row_el) -> Optional[Quote]:
        dom = self.parse_dom(row_el)
        if dom and dom.get("quote"):
            raw = dom["quote"]
            return Quote(
                sender=self._parse_sender(raw.get("sender")),
                text=(raw.get("text") or "").strip(),
            )

        try:
            raw = self.driver.execute_script(_JS_QUOTE_PARSE, row_el)
            if raw:
                return Quote(
                    sender=self._parse_sender(raw.get("sender")),
                    text=(raw.get("text") or "").strip(),
                )
        except Exception:
            pass

        block = self.find_quote_block(row_el)
        if block is None:
            return None

        author_el = find(block, '[data-testid="quoted-author"]')
        if author_el is None:
            for sp in find_all(block, '[testid="author"], [data-testid="author"]'):
                author_el = sp
                break
        text_el = find(block, S["quote_text"])
        q_sender_raw = author_el.text.strip() if author_el else ""
        q_sender = self._parse_sender(q_sender_raw)
        q_text = text_el.text.strip() if text_el else ""

        if not q_text:
            for sp in find_all(block, "span[dir], div[dir]"):
                t = sp.text.strip()
                if t and t != q_sender_raw and not re.match(r"^\d{1,2}:\d{2}", t):
                    q_text = t
                    break

        if not q_sender.user_name and not q_sender.phone and not q_text:
            return None
        return Quote(sender=q_sender, text=q_text)

    def parse(
        self,
        row_el,
        current_date: str,
    ) -> Optional[Message]:
        try:
            mid = self.message_id(row_el)
            if not mid:
                return None

            if self.is_notification_row(row_el):
                return None

            dom = self.parse_dom(row_el)

            is_out = (
                dom.get("isOutgoing")
                if dom and "isOutgoing" in dom
                else self.is_outgoing(row_el)
            )

            quote = self.parse_quote(row_el)
            if dom and dom.get("sender"):
                sender = self._parse_sender(dom.get("sender"))
            else:
                sender = self.extract_sender(row_el, is_out)

            msg_time = ""
            if dom and dom.get("time"):
                msg_time = str(dom["time"]).strip()
            if not msg_time:
                meta_el = find(row_el, S["msg_meta"])
                if meta_el:
                    raw = meta_el.text.strip()
                    m = re.search(r"\d{1,2}:\d{2}(?:\s?[AaPp][Mm])?", raw)
                    msg_time = m.group(0) if m else raw.split()[-1]

            text = (
                str(dom.get("text") or "").strip()
                if dom and dom.get("text")
                else self.extract_text(row_el)
            )

            media_type = None
            if not text:
                for mtype, sel in MEDIA_CHECKS:
                    if find(row_el, sel):
                        media_type = mtype
                        break

            if (
                not sender.user_name
                and not sender.phone
                and not text
                and media_type is None
            ):
                return None

            if media_type is None and not re.search(
                r"\d{1,2}:\d{2}", msg_time
            ):
                return None

            scope = self.reaction_scope(row_el)
            reactions = self._reactions.parse(scope)

            return Message(
                id=mid,
                date=current_date,
                time=msg_time,
                sender=sender,
                text=text,
                is_outgoing=is_out,
                quote=quote,
                reactions=reactions,
                media_type=media_type,
            )

        except StaleElementReferenceException:
            return None
