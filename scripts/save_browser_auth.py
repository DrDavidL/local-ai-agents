#!/usr/bin/env python3
"""One-time setup: log into paywalled sites and save browser auth state.

This opens a visible Chromium browser. Log into WSJ, NYT, and any other
paywalled sites, then close the browser. The auth state (cookies, localStorage)
is saved to data/browser_auth.json for use by the current_events agent.

Usage:
    uv sync --extra scraping
    playwright install chromium
    uv run python scripts/save_browser_auth.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

AUTH_STATE_FILE = Path(__file__).parent.parent / "data" / "browser_auth.json"

SITES_TO_LOGIN = [
    ("WSJ", "https://www.wsj.com"),
    ("NYT", "https://www.nytimes.com"),
]


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run:")
        print("  uv sync --extra scraping")
        print("  playwright install chromium")
        sys.exit(1)

    AUTH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    print("\nThis will open a browser window.")
    print("Please log into each site when prompted, then press Enter to continue.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        for name, url in SITES_TO_LOGIN:
            print(f"Opening {name}: {url}")
            page.goto(url, wait_until="domcontentloaded")
            input(f"  Log into {name} if needed, then press Enter to continue...")

        # Save auth state
        context.storage_state(path=str(AUTH_STATE_FILE))
        browser.close()

    print(f"\nAuth state saved to: {AUTH_STATE_FILE}")
    print("You can now enable paywalled sources in config/feeds.yaml")


if __name__ == "__main__":
    main()
