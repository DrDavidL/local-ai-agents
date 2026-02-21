"""bioRxiv RSS feed client."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import feedparser

logger = logging.getLogger(__name__)

BASE_URL = "https://connect.biorxiv.org/biorxiv_xml.php"


@dataclass
class BiorxivPaper:
    doi: str
    title: str
    authors: str
    abstract: str
    published: str
    url: str


def fetch(subject: str = "bioinformatics", max_results: int = 20) -> list[BiorxivPaper]:
    """Fetch recent bioRxiv papers from RSS feed."""
    url = f"{BASE_URL}?subject={subject}"
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            logger.error("bioRxiv feed parse error: %s", feed.bozo_exception)
            return []

        papers = []
        for entry in feed.entries[:max_results]:
            doi = entry.get("dc_identifier", entry.get("id", ""))
            title = entry.get("title", "").strip()
            authors = entry.get("author", entry.get("dc_creator", ""))
            abstract = entry.get("summary", "")[:2000]
            published = entry.get("published", entry.get("updated", ""))[:10]
            link = entry.get("link", "")

            papers.append(BiorxivPaper(
                doi=doi,
                title=title,
                authors=authors,
                abstract=abstract,
                published=published,
                url=link,
            ))
        return papers
    except Exception as exc:
        logger.error("bioRxiv fetch failed: %s", exc)
        return []
