"""NIH RePORTER API client for funding opportunity announcements."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.reporter.nih.gov/v2/projects/search"
TIMEOUT = 30.0


@dataclass
class NIHOpportunity:
    foa_number: str
    title: str
    agency: str
    ic_code: str
    funding_range: str
    deadline: str
    url: str
    abstract: str


def search(
    keywords: list[str],
    activity_codes: list[str] | None = None,
    ic_codes: list[str] | None = None,
    max_results: int = 25,
) -> list[NIHOpportunity]:
    """Search NIH RePORTER for recent funding opportunities."""
    criteria: dict = {
        "advanced_text_search": {
            "operator": "or",
            "search_field": "terms",
            "search_text": " ".join(keywords),
        },
        "limit": max_results,
        "offset": 0,
        "sort_field": "project_start_date",
        "sort_order": "desc",
        "include_active_projects": True,
        "fiscal_years": [2025, 2026],
    }

    if activity_codes:
        criteria["activity_codes"] = activity_codes
    if ic_codes:
        criteria["agency_ic_admin"] = {"include_values": ic_codes}

    try:
        resp = httpx.post(BASE_URL, json={"criteria": criteria}, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return _parse_results(data)
    except Exception as exc:
        logger.error("NIH RePORTER search failed: %s", exc)
        return []


def _parse_results(data: dict) -> list[NIHOpportunity]:
    """Parse NIH RePORTER API response."""
    results = data.get("results", [])
    opportunities = []

    for item in results:
        try:
            title = item.get("project_title", "")
            agency = item.get("agency_ic_fundings", [{}])
            ic_code = agency[0].get("abbreviation", "") if agency else ""
            award_amount = item.get("award_amount")
            funding_range = f"${award_amount:,.0f}" if award_amount else "Not specified"
            abstract = (item.get("abstract_text") or "")[:1500]

            project_num = item.get("project_num", "")
            url = f"https://reporter.nih.gov/project-details/{project_num}" if project_num else ""

            opportunities.append(NIHOpportunity(
                foa_number=item.get("opportunity_number", project_num),
                title=title,
                agency="NIH",
                ic_code=ic_code,
                funding_range=funding_range,
                deadline="",  # RePORTER doesn't always have deadline info
                url=url,
                abstract=abstract,
            ))
        except Exception as exc:
            logger.warning("Failed to parse NIH result: %s", exc)
            continue

    return opportunities
