"""Browser session management and WhatsApp Web login."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException

from wa_scraper.selectors import APP_READY_SELECTORS, QR_CODE_SELECTORS
from wa_scraper.utils import jitter, log, wait_for_any_selector


class BrowserSession:
    """Manages an undetected Chrome instance with a persistent WhatsApp profile."""

    DEFAULT_PROFILE = Path.home() / ".wa_scraper_profile"

    def __init__(self, profile_dir: Optional[str] = None) -> None:
        self.profile_dir = (Path(profile_dir) if profile_dir else self.DEFAULT_PROFILE)
        self.driver: uc.Chrome | None = None

    def start(self) -> uc.Chrome:
        """Create and return a configured Chrome driver."""
        opts = uc.ChromeOptions()
        opts.add_argument("--start-maximized")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--lang=he,en-US,en")
        opts.add_argument("--no-first-run")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        opts.add_argument(f"--user-data-dir={self.profile_dir}")

        self.driver = uc.Chrome(options=opts, version_main=None)
        if self.driver is None:
            raise RuntimeError(f"Failed to open chrome profile: {opts.__dict__}")
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
        )
        return self.driver

    def open_whatsapp(self) -> None:
        """Navigate to WhatsApp Web."""
        if self.driver is None:
            raise RuntimeError("Call start() before open_whatsapp()")
        self.driver.get("https://web.whatsapp.com/")

    def wait_for_login(self, timeout: int = 180) -> None:
        """Block until the chat-list is visible (= fully logged in)."""
        if self.driver is None:
            raise RuntimeError("Driver not started")

        log("Loading WhatsApp Web …")

        try:
            wait_for_any_selector(self.driver, QR_CODE_SELECTORS, timeout=25)
            log("┌─────────────────────────────────────────────┐")
            log("│  Scan the QR code in the browser with your  │")
            log("│  phone to log in to WhatsApp Web.           │")
            log("└─────────────────────────────────────────────┘")
        except TimeoutException:
            log("No QR screen detected – checking for an existing session …")

        wait_for_any_selector(self.driver, APP_READY_SELECTORS, timeout=timeout)
        log("Session active ✓")
        jitter(1.5, 2.5)

    def quit(self) -> None:
        """Close the browser."""
        if self.driver is not None:
            self.driver.quit()
            self.driver = None
