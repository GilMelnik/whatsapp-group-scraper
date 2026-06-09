"""Top-level WhatsApp group chat scraper orchestration."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional

from wa_scraper.browser import BrowserSession
from wa_scraper.harvester import MessageHarvester
from wa_scraper.models import Message
from wa_scraper.navigation import ChatNavigator
from wa_scraper.utils import log


@dataclass
class ScraperConfig:
    """Runtime options for a scrape session."""

    group: str
    output: str = "wa_chat.json"
    profile_dir: Optional[str] = None
    scroll_pause: float = 1.0
    max_top_scrolls: int = 6000
    max_down_passes: int = 2500
    reactions_detail: bool = True


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

            harvester = MessageHarvester(
                driver,
                reactions_detail=self.config.reactions_detail,
                scroll_pause=self.config.scroll_pause,
                max_top_scrolls=self.config.max_top_scrolls,
                max_down_passes=self.config.max_down_passes,
            )
            return harvester.scrape()
        finally:
            input("\nPress Enter to close the browser …")
            self._session.quit()
