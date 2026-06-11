"""Validate _JS_PARSE_MESSAGE against a captured panel.html (offline, visible window).

Loads a saved capture as a local file and runs the production message parser on
every conversation row, so we can confirm caption text, forwarded/deleted flags,
media type, and link previews without touching WhatsApp. The browser opens in a
normal visible window (never headless).

Usage:
    python -m tools.validate_parse captures/<run>/00_initial/panel.html
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import undetected_chromedriver as uc

from wa_scraper.js.message import _JS_PARSE_MESSAGE

_ROW_SELECTOR = '[data-testid^="conv-msg-"][data-id]'


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m tools.validate_parse <panel.html>")
        sys.exit(2)

    path = Path(sys.argv[1]).resolve()
    if not path.is_file():
        print(f"no such file: {path}")
        sys.exit(2)

    opts = uc.ChromeOptions()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    driver = uc.Chrome(options=opts, version_main=None)
    try:
        driver.get(path.as_uri())
        rows = driver.find_elements("css selector", _ROW_SELECTOR)
        print(f"rows found: {len(rows)}\n")

        n_caption = n_fwd = n_del = n_media = n_link = 0
        for row in rows:
            try:
                dom = driver.execute_script(_JS_PARSE_MESSAGE, row)
            except Exception as exc:
                print(f"  parse error: {exc}")
                continue
            if not dom:
                continue
            flags = []
            if dom.get("isForwarded"):
                flags.append("FWD")
                n_fwd += 1
            if dom.get("isDeleted"):
                flags.append("DEL")
                n_del += 1
            if dom.get("mediaType"):
                flags.append(f"media={dom['mediaType']}")
                n_media += 1
            if dom.get("linkPreview"):
                flags.append("link")
                n_link += 1
            text = (dom.get("text") or "").replace("\n", " ")
            if flags:
                print(f"  [{','.join(flags)}] text={text[:70]!r}")
            if dom.get("mediaType") and text:
                n_caption += 1
                print(f"    -> CAPTIONED MEDIA: {text[:90]!r}")
            if dom.get("linkPreview"):
                print(f"    -> link_preview: {json.dumps(dom['linkPreview'], ensure_ascii=False)[:120]}")

        print(
            f"\nsummary: forwarded={n_fwd} deleted={n_del} media={n_media} "
            f"captioned={n_caption} link_previews={n_link}"
        )
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
