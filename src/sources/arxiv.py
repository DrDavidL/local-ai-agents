"""arXiv API client (Atom feed)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://export.arxiv.org/api/query"
TIMEOUT = 30.0


@dataclass
class ArxivPaper:
    arxiv_id: str
    title: str
    authors: str
    abstract: str
    categories: list[str]
    published: str
    url: str


def search(
    query: str,
    categories: list[str] | None = None,
    max_results: int = 20,
) -> list[ArxivPaper]:
    """Search arXiv and return parsed papers."""
    search_query = query
    if categories:
        cat_filter = " OR ".join(f"cat:{c}" for c in categories)
        search_query = f"({query}) AND ({cat_filter})"

    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    try:
        resp = httpx.get(BASE_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return _parse_atom(resp.text)
    except Exception as exc:
        logger.error("arXiv search failed: %s", exc)
        return []


def _parse_atom(xml_text: str) -> list[ArxivPaper]:
    """Parse arXiv Atom XML response."""
    import xml.etree.ElementTree as ET

    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    papers = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.error("arXiv XML parse error: %s", exc)
        return []

    for entry in root.findall("atom:entry", ns):
        try:
            arxiv_id = entry.findtext("atom:id", "", ns).split("/abs/")[-1]
            title = entry.findtext("atom:title", "", ns).replace("\n", " ").strip()
            abstract = entry.findtext("atom:summary", "", ns).strip()[:2000]
            published = entry.findtext("atom:published", "", ns)[:10]

            authors = []
            for author in entry.findall("atom:author", ns):
                name = author.findtext("atom:name", "", ns)
                if name:
                    authors.append(name)
            authors_str = ", ".join(authors[:3])
            if len(authors) > 3:
                authors_str += " et al."

            categories = []
            for cat in entry.findall("atom:category", ns):
                term = cat.get("term", "")
                if term:
                    categories.append(term)

            link = ""
            for link_el in entry.findall("atom:link", ns):
                if link_el.get("type") == "text/html":
                    link = link_el.get("href", "")
                    break
            if not link:
                link = f"https://arxiv.org/abs/{arxiv_id}"

            papers.append(ArxivPaper(
                arxiv_id=arxiv_id,
                title=title,
                authors=authors_str,
                abstract=abstract,
                categories=categories,
                published=published,
                url=link,
            ))
        except Exception as exc:
            logger.warning("Failed to parse arXiv entry: %s", exc)
            continue

    return papers
