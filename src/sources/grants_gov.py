"""Grants.gov RSS feed client."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import feedparser

logger = logging.getLogger(__name__)

RSS_URL = "https://www.grants.gov/rss/GG_NewOppByCategory.xml"


@dataclass
class GrantsGovOpportunity:
    opportunity_id: str
    title: str
    agency: str
    deadline: str
    url: str
    description: str


def search(
    keywords: list[str] | None = None,
    categories: list[str] | None = None,
    max_results: int = 25,
) -> list[GrantsGovOpportunity]:
    """Fetch and filter opportunities from Grants.gov RSS feed."""
    try:
        feed = feedparser.parse(RSS_URL)
        if feed.bozo and not feed.entries:
            logger.error("Grants.gov feed parse error: %s", feed.bozo_exception)
            return []

        opportunities = []
        keywords_lower = [k.lower() for k in (keywords or [])]

        for entry in feed.entries:
            title = entry.get("title", "")
            description = entry.get("summary", "")
            combined = f"{title} {description}".lower()

            # Filter by keywords if provided
            if keywords_lower and not any(kw in combined for kw in keywords_lower):
                continue

            opp_id = entry.get("id", entry.get("link", ""))
            deadline = entry.get("published", "")
            link = entry.get("link", "")

            opportunities.append(GrantsGovOpportunity(
                opportunity_id=opp_id,
                title=title.strip(),
                agency=entry.get("author", ""),
                deadline=deadline[:25],
                url=link,
                description=description[:1500],
            ))

            if len(opportunities) >= max_results:
                break

        return opportunities
    except Exception as exc:
        logger.error("Grants.gov fetch failed: %s", exc)
        return []
