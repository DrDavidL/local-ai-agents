"""PubMed E-utilities API client.

Rate limits:
  - Without API key: 3 requests/sec
  - With API key: 10 requests/sec
Set PUBMED_API_KEY env var to use higher limit.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TIMEOUT = 30.0

# Track last request time for rate limiting
_last_request_time: float = 0.0


def _get_api_params() -> dict:
    """Return api_key param if PUBMED_API_KEY is set."""
    key = os.environ.get("PUBMED_API_KEY", "")
    return {"api_key": key} if key else {}


def _rate_limit() -> None:
    """Enforce rate limiting between requests."""
    global _last_request_time
    key = os.environ.get("PUBMED_API_KEY", "")
    min_interval = 0.11 if key else 0.35  # 10/sec with key, ~3/sec without
    elapsed = time.monotonic() - _last_request_time
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time = time.monotonic()


@dataclass
class PubMedArticle:
    pmid: str
    title: str
    authors: str
    abstract: str
    source: str
    pub_date: str
    url: str


def search(query: str, max_results: int = 20) -> list[str]:
    """Search PubMed and return a list of PMIDs."""
    _rate_limit()
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "sort": "date",
        "retmode": "json",
        "datetype": "edat",
        "reldate": 7,  # last 7 days
        **_get_api_params(),
    }
    try:
        resp = httpx.get(f"{BASE_URL}/esearch.fcgi", params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("esearchresult", {}).get("idlist", [])
    except Exception as exc:
        logger.error("PubMed search failed: %s", exc)
        return []


def fetch_details(pmids: list[str]) -> list[PubMedArticle]:
    """Fetch article details for a list of PMIDs."""
    if not pmids:
        return []

    _rate_limit()
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "rettype": "abstract",
        **_get_api_params(),
    }
    try:
        resp = httpx.get(f"{BASE_URL}/efetch.fcgi", params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return _parse_xml(resp.text)
    except Exception as exc:
        logger.error("PubMed fetch failed: %s", exc)
        return []


def _parse_xml(xml_text: str) -> list[PubMedArticle]:
    """Parse PubMed XML response into article objects."""
    import xml.etree.ElementTree as ET
    from defusedxml.ElementTree import fromstring as safe_fromstring

    articles = []
    try:
        root = safe_fromstring(xml_text)
    except ET.ParseError as exc:
        logger.error("PubMed XML parse error: %s", exc)
        return []

    for article_el in root.findall(".//PubmedArticle"):
        try:
            medline = article_el.find("MedlineCitation")
            pmid = medline.findtext("PMID", "")
            art = medline.find("Article")
            title = art.findtext("ArticleTitle", "")

            # Authors
            author_list = art.find("AuthorList")
            authors = []
            if author_list is not None:
                for author in author_list.findall("Author"):
                    last = author.findtext("LastName", "")
                    initials = author.findtext("Initials", "")
                    if last:
                        authors.append(f"{last} {initials}".strip())
            authors_str = ", ".join(authors[:3])
            if len(authors) > 3:
                authors_str += " et al."

            # Abstract
            abstract_el = art.find("Abstract")
            abstract = ""
            if abstract_el is not None:
                parts = [t.text or "" for t in abstract_el.findall("AbstractText")]
                abstract = " ".join(parts)

            # Source and date
            journal = art.find("Journal")
            source = journal.findtext("Title", "") if journal is not None else ""
            pub_date_el = journal.find(".//PubDate") if journal is not None else None
            pub_date = ""
            if pub_date_el is not None:
                year = pub_date_el.findtext("Year", "")
                month = pub_date_el.findtext("Month", "")
                pub_date = f"{year} {month}".strip()

            articles.append(PubMedArticle(
                pmid=pmid,
                title=title,
                authors=authors_str,
                abstract=abstract[:2000],  # Truncate for LLM context
                source=source,
                pub_date=pub_date,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            ))
        except Exception as exc:
            logger.warning("Failed to parse PubMed article: %s", exc)
            continue

    return articles


def search_and_fetch(query: str, max_results: int = 20) -> list[PubMedArticle]:
    """Search and fetch articles in one call."""
    pmids = search(query, max_results)
    return fetch_details(pmids)
