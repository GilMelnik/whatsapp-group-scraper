"""Top-level WhatsApp group chat scraper orchestration."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional

from wa_scraper.browser import BrowserSession
from wa_scraper.harvester import MessageHarvester
from wa_scraper.models import Message
from wa_scraper.navigation import ChatNavigator
from wa_scraper.postprocess import parse_chat_date
from wa_scraper.storage import ShardWriter
from wa_scraper.utils import log


@dataclass
class ScraperConfig:
    """Runtime options for a scrape session."""

    group: str
    output: str = "wa_chat.json"
    profile_dir: Optional[str] = None
    scroll_pause: float = 1.0
    max_top_scrolls: int = 6000
    scroll_to_chat_start: bool = False
    shard_size: int = 5000
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    resume: bool = True
    checkpoint_interval: int = 25


class WhatsAppScraper:
    """Orchestrates browser login, chat navigation, and message harvesting."""

    def __init__(self, config: ScraperConfig) -> None:
        self.config = config
        self._session = BrowserSession(config.profile_dir)

    def scrape(self) -> List[Message]:
        driver = self._session.start()
        try:
            self._session.open_whatsapp()
            self._session.wait_for_login()

            navigator = ChatNavigator(driver)
            if not navigator.open(self.config.group):
                log("Could not open the group.  Exiting.")
                sys.exit(1)

            from_date = parse_chat_date(self.config.from_date or "")
            to_date = parse_chat_date(self.config.to_date or "")
            if self.config.from_date and from_date is None:
                log(f"  ⚠  Could not parse --from-date {self.config.from_date!r}; ignoring.")
            if self.config.to_date and to_date is None:
                log(f"  ⚠  Could not parse --to-date {self.config.to_date!r}; ignoring.")

            writer = ShardWriter(
                self.config.output,
                shard_size=self.config.shard_size,
                resume=self.config.resume,
            )

            harvester = MessageHarvester(
                driver,
                scroll_pause=self.config.scroll_pause,
                max_top_scrolls=self.config.max_top_scrolls,
                scroll_to_chat_start=self.config.scroll_to_chat_start,
                writer=writer,
                from_date=from_date,
                to_date=to_date,
                checkpoint_interval=self.config.checkpoint_interval,
            )
            return harvester.scrape()
        finally:
            self._session.quit()
