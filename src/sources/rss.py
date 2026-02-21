"""Generic RSS/Atom feed reader."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import feedparser

logger = logging.getLogger(__name__)


@dataclass
class FeedItem:
    title: str
    url: str
    source: str
    summary: str
    published: str


def fetch_feed(url: str, source_name: str = "", max_items: int = 20) -> list[FeedItem]:
    """Parse an RSS/Atom feed and return items."""
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            logger.error("Feed parse error for %s: %s", url, feed.bozo_exception)
            return []

        source = source_name or feed.feed.get("title", url)
        items = []
        for entry in feed.entries[:max_items]:
            items.append(FeedItem(
                title=entry.get("title", "").strip(),
                url=entry.get("link", ""),
                source=source,
                summary=entry.get("summary", "")[:1000],
                published=entry.get("published", entry.get("updated", ""))[:25],
            ))
        return items
    except Exception as exc:
        logger.error("Feed fetch failed for %s: %s", url, exc)
        return []


def fetch_multiple(feeds: list[dict], max_per_feed: int = 10) -> list[FeedItem]:
    """Fetch multiple feeds. Each dict should have 'url' and optionally 'name'."""
    all_items = []
    for feed_conf in feeds:
        url = feed_conf.get("url", "")
        name = feed_conf.get("name", "")
        if url:
            all_items.extend(fetch_feed(url, name, max_per_feed))
    return all_items
