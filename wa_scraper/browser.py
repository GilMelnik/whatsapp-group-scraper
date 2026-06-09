"""Browser session management and WhatsApp Web login."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import undetected_chromedriver as uc
from selenium.common.exceptions import SessionNotCreatedException, TimeoutException

from wa_scraper.selectors import APP_READY_SELECTORS, QR_CODE_SELECTORS
from wa_scraper.utils import jitter, log, wait_for_any_selector

_MAX_PREFERENCES_BYTES = 50 * 1024 * 1024  # 50 MB – real files are usually < 1 MB


class BrowserSession:
    """Manages an undetected Chrome instance with a persistent WhatsApp profile."""

    DEFAULT_PROFILE = Path.home() / ".wa_scraper_profile"

    def __init__(self, profile_dir: Optional[str] = None) -> None:
        self.profile_dir = (Path(profile_dir) if profile_dir else self.DEFAULT_PROFILE)
        self.driver: uc.Chrome | None = None

    def _clear_stale_singleton_locks(self) -> None:
        """Remove Chrome singleton files left behind by a crashed session."""
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            path = self.profile_dir / name
            if not path.exists():
                continue
            try:
                path.unlink()
            except OSError:
                pass

    def _quarantine_bloated_preferences(self) -> None:
        """Move an oversized Preferences file that prevents Chrome from starting."""
        prefs = self.profile_dir / "Default" / "Preferences"
        if not prefs.is_file():
            return
        try:
            size = prefs.stat().st_size
        except OSError:
            return
        if size <= _MAX_PREFERENCES_BYTES:
            return
        backup = prefs.with_name(f"Preferences.corrupted.{int(time.time())}")
        prefs.rename(backup)
        log(
            f"Moved bloated Chrome Preferences ({size / (1024 * 1024):.0f} MB) "
            f"to {backup.name}; Chrome will recreate it."
        )

    def _prepare_profile(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._clear_stale_singleton_locks()
        self._quarantine_bloated_preferences()

    def start(self) -> uc.Chrome:
        """Create and return a configured Chrome driver."""
        self._prepare_profile()

        opts = uc.ChromeOptions()
        opts.add_argument("--start-maximized")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--lang=he,en-US,en")
        opts.add_argument("--no-first-run")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")

        opts.add_argument(f"--user-data-dir={self.profile_dir}")

        try:
            self.driver = uc.Chrome(options=opts, version_main=None)
        except SessionNotCreatedException as exc:
            if "chrome not reachable" in str(exc).lower():
                raise RuntimeError(
                    f"Chrome failed to start with profile {self.profile_dir}. "
                    "Try deleting the profile directory or passing --profile-dir "
                    "with a fresh path. You will need to scan the QR code again."
                ) from exc
            raise
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
