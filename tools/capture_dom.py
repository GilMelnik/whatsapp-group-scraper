"""Capture WhatsApp Web message-panel DOM at several scroll positions.

This is a developer/analysis side-script. It reuses the production scraper's
session, navigation, and scrolling so the snapshots reflect exactly what the
real scraper sees. Nothing is parsed here -- we only dump raw artifacts:

  - page_source           (whole document)
  - panel outerHTML       (the conversation-panel-messages subtree)
  - metrics JSON          (PanelScroller.metrics(): scroll geometry + visible ids)
  - visible chat items    (the same list harvest_visible() consumes)

Snapshots are written to ./captures/<run-ts>/NN_<label>/ so we can diff how the
virtual list windows rows in and out as we scroll upward.

Usage:
    python -m tools.capture_dom --group "My Group"
    python -m tools.capture_dom --group "My Group" --steps 4 --scrolls-per-step 3
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from wa_scraper.browser import BrowserSession
from wa_scraper.js.scroll import _JS_CHAT_ITEMS
from wa_scraper.navigation import ChatNavigator
from wa_scraper.scroll.panel import PanelScroller
from wa_scraper.selectors import S
from wa_scraper.utils import jitter, log

CAPTURES_ROOT = Path(__file__).resolve().parent.parent / "captures"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _visible_chat_items(driver, panel) -> list:
    try:
        result = driver.execute_script(_JS_CHAT_ITEMS, panel)
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _serialize_items(items: list) -> list:
    """Drop the live WebElement refs so the items are JSON-serializable."""
    out = []
    for item in items:
        clean = {k: v for k, v in item.items() if k != "el"}
        clean["has_el"] = "el" in item and item["el"] is not None
        out.append(clean)
    return out


def snapshot(driver, panel, scroller: PanelScroller, out_dir: Path, label: str) -> dict:
    """Dump the current DOM state into out_dir and return a small summary."""
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics = scroller.metrics()
    items = _visible_chat_items(driver, panel)

    try:
        panel_html = driver.execute_script("return arguments[0].outerHTML;", panel)
    except Exception:
        panel_html = ""

    _write(out_dir / "page_source.html", driver.page_source or "")
    _write(out_dir / "panel.html", panel_html or "")
    _write(out_dir / "metrics.json", json.dumps(metrics, ensure_ascii=False, indent=2))
    _write(
        out_dir / "visible_items.json",
        json.dumps(_serialize_items(items), ensure_ascii=False, indent=2),
    )

    summary = {
        "label": label,
        "visibleCount": metrics.get("visibleCount"),
        "scrollTop": metrics.get("scrollTop"),
        "scrollHeight": metrics.get("scrollHeight"),
        "clientHeight": metrics.get("clientHeight"),
        "reversed": metrics.get("reversed"),
        "atTop": metrics.get("atTop"),
        "atBottom": metrics.get("atBottom"),
        "oldestId": metrics.get("oldestId"),
        "item_kinds": _kind_counts(items),
        "panel_html_bytes": len(panel_html or ""),
    }
    log(
        f"  • {label}: visible={summary['visibleCount']} "
        f"items={summary['item_kinds']} scrollTop={summary['scrollTop']} "
        f"panelHTML={summary['panel_html_bytes']}B"
    )
    return summary


def _kind_counts(items: list) -> dict:
    counts: dict = {}
    for item in items:
        kind = item.get("kind", "?")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description="Capture WhatsApp Web DOM snapshots.")
    ap.add_argument("--group", required=True, help="Group name to open.")
    ap.add_argument("--profile-dir", default=None, help="Chrome profile dir.")
    ap.add_argument(
        "--steps",
        type=int,
        default=4,
        help="How many snapshot checkpoints to take while scrolling up.",
    )
    ap.add_argument(
        "--scrolls-per-step",
        type=int,
        default=3,
        help="How many upward scroll nudges between checkpoints.",
    )
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass

    run_dir = CAPTURES_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    log(f"Artifacts → {run_dir}")

    session = BrowserSession(args.profile_dir)
    driver = session.start()
    summaries: list = []
    try:
        session.open_whatsapp()
        session.wait_for_login()

        navigator = ChatNavigator(driver)
        if not navigator.open(args.group):
            log("Could not open the group. Exiting.")
            sys.exit(1)

        panel = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, S["panel"]))
        )
        scroller = PanelScroller(driver, panel)

        # Clear the cached reversed-flag the scraper sets, mirroring harvester.
        try:
            driver.execute_script("delete arguments[0].dataset.waReversed;", panel)
        except Exception:
            pass

        jitter(0.8, 1.4)
        summaries.append(
            snapshot(driver, panel, scroller, run_dir / "00_initial", "initial")
        )

        for step in range(1, args.steps + 1):
            for _ in range(max(1, args.scrolls_per_step)):
                scroller.wait_no_spinner()
                metrics = scroller.metrics()
                anchor = scroller.topmost_anchor_id({}, metrics)
                result = scroller.scroll_up_from_anchor(anchor)
                if result.get("stuck"):
                    scroller.scroll_fallback("up")
                jitter(0.4, 0.8)

            label = f"up_{step}"
            summaries.append(
                snapshot(
                    driver,
                    panel,
                    scroller,
                    run_dir / f"{step:02d}_{label}",
                    label,
                )
            )

        _write(
            run_dir / "summary.json",
            json.dumps(summaries, ensure_ascii=False, indent=2),
        )
        log(f"\n✓  Captured {len(summaries)} snapshots → {run_dir}")
    finally:
        session.quit()


if __name__ == "__main__":
    main()
