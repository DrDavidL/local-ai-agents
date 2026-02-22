"""Playwright browser source for paywalled content extraction.

Optional dependency: install with `uv sync --extra scraping && playwright install chromium`
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

AUTH_STATE_FILE = Path(__file__).parent.parent.parent / "data" / "browser_auth.json"


@dataclass
class ScrapedArticle:
    title: str
    url: str
    source: str
    snippet: str


def is_available() -> bool:
    """Check if Playwright is installed."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def scrape_headlines(
    url: str,
    source_name: str = "",
    max_items: int = 10,
    use_auth: bool = False,
) -> list[ScrapedArticle]:
    """Scrape headlines from a news site using Playwright.

    Args:
        url: The page URL to scrape.
        source_name: Human-readable source name.
        max_items: Maximum number of articles to return.
        use_auth: If True, load saved browser auth state for paywalled sites.
    """
    if not is_available():
        logger.warning("Playwright not installed. Run: uv sync --extra scraping && playwright install chromium")
        return []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright import failed")
        return []

    auth_path = AUTH_STATE_FILE if use_auth and AUTH_STATE_FILE.exists() else None

    articles = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context_kwargs = {}
            if auth_path:
                context_kwargs["storage_state"] = str(auth_path)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Extract article links — common patterns across news sites
            links = page.query_selector_all(
                "article a, h2 a, h3 a, [class*='headline'] a, [class*='story'] a"
            )

            seen_urls: set[str] = set()
            for link in links:
                href = link.get_attribute("href") or ""
                text = (link.inner_text() or "").strip()

                if not text or len(text) < 10:
                    continue

                # Resolve relative URLs
                if href.startswith("/"):
                    from urllib.parse import urljoin
                    href = urljoin(url, href)
                elif not href.startswith("http"):
                    continue

                if href in seen_urls:
                    continue
                seen_urls.add(href)

                articles.append(ScrapedArticle(
                    title=text[:200],
                    url=href,
                    source=source_name or url,
                    snippet="",
                ))

                if len(articles) >= max_items:
                    break

            browser.close()

    except Exception as exc:
        logger.error("Playwright scrape failed for %s: %s", url, exc)

    return articles
