"""Grant opportunity scanner agent."""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any

from src.agents.base import BaseAgent, load_grants_config
from src.sources import nih_reporter, grants_gov
from src import delivery

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a grants specialist for an academic physician-informaticist. Below are \
numbered funding opportunities. For each, provide ONLY your analysis — do NOT repeat the title, \
FOA number, agency, deadline, funding range, or URL (those are already known).

Return JSON with this exact structure:
{
  "summary": "2-3 sentence overview of this week's opportunities",
  "assessments": [
    {
      "item_number": 1,
      "relevance": "high|medium|low",
      "one_liner": "What they're funding in one sentence",
      "fit_assessment": "Why this matches or doesn't match the PI's clinical informatics + AI profile"
    }
  ]
}

Only include medium and high relevance opportunities."""


class GrantsAgent(BaseAgent):
    name = "grants"

    def fetch(self) -> list[dict]:
        grants_config = load_grants_config()
        keywords = grants_config.get("search_terms", [])
        nih_config = grants_config.get("nih", {})
        gov_config = grants_config.get("grants_gov", {})

        all_opportunities: list[dict] = []

        # NIH RePORTER
        nih_results = nih_reporter.search(
            keywords=keywords,
            activity_codes=nih_config.get("activity_codes"),
            ic_codes=nih_config.get("ic_codes"),
            max_results=20,
        )
        for r in nih_results:
            all_opportunities.append({
                "id": r.foa_number or r.title[:50],
                "title": r.title,
                "foa_number": r.foa_number,
                "agency": r.agency,
                "ic_code": r.ic_code,
                "funding_range": r.funding_range,
                "deadline": r.deadline,
                "url": r.url,
                "abstract": r.abstract,
            })

        # Grants.gov
        gov_results = grants_gov.search(
            keywords=keywords,
            categories=gov_config.get("categories"),
            max_results=20,
        )
        for r in gov_results:
            all_opportunities.append({
                "id": r.opportunity_id or r.title[:50],
                "title": r.title,
                "foa_number": r.opportunity_id,
                "agency": r.agency,
                "ic_code": "",
                "funding_range": "See listing",
                "deadline": r.deadline,
                "url": r.url,
                "abstract": r.description,
            })

        return all_opportunities

    def dedup(self, items: list[dict], seen_ids: set[str]) -> list[dict]:
        return [item for item in items if item["id"] not in seen_ids]

    def extract_ids(self, items: list[dict]) -> list[str]:
        return [item["id"] for item in items]

    def summarize(self, items: list[dict]) -> dict[str, Any] | None:
        limited = items[:self.max_items * 2]

        # Build numbered list — no URLs sent to LLM
        content_parts = []
        for i, item in enumerate(limited, 1):
            content_parts.append(
                f"Item {i}:\n"
                f"Title: {item['title']}\n"
                f"FOA/ID: {item['foa_number']}\n"
                f"Agency: {item['agency']} {item['ic_code']}\n"
                f"Funding: {item['funding_range']}\n"
                f"Deadline: {item['deadline']}\n"
                f"Description: {item['abstract'][:400]}\n"
            )
        content = "\n---\n".join(content_parts)
        if len(content) > 5000:
            content = content[:5000] + "\n[truncated]"

        llm_result = self._llm_summarize(SYSTEM_PROMPT, content)
        if not llm_result:
            return None

        # Merge LLM assessments with source data
        assessments = {a["item_number"]: a for a in llm_result.get("assessments", [])}
        merged_opps = []
        for i, item in enumerate(limited, 1):
            if i in assessments:
                a = assessments[i]
                merged_opps.append({
                    "title": item["title"],
                    "foa_number": item["foa_number"],
                    "agency": item["agency"],
                    "ic_code": item["ic_code"],
                    "funding_range": item["funding_range"],
                    "deadline": item["deadline"],
                    "url": item["url"],
                    "relevance": a.get("relevance", ""),
                    "one_liner": a.get("one_liner", ""),
                    "fit_assessment": a.get("fit_assessment", ""),
                })

        return {
            "summary": llm_result.get("summary", ""),
            "opportunities": merged_opps,
        }

    def deliver(self, result: dict[str, Any]) -> None:
        opportunities = result.get("opportunities", [])
        today = datetime.now().strftime("%Y-%m-%d")

        high_rel = [o for o in opportunities if o.get("relevance") == "high"]
        deadlines = [o.get("deadline", "TBD") for o in high_rel[:3]]

        if "text" in self.delivery_methods:
            msg = (
                f"{len(opportunities)} new grant opportunities this week"
                f", {len(high_rel)} high relevance"
            )
            if deadlines:
                msg += f" (deadline {deadlines[0]})"
            delivery.send_text(msg)

        if "email" in self.delivery_methods:
            html = self._build_html(result)
            delivery.send_email(f"Grant Opportunities - {today}", html)

        if "file" in self.delivery_methods:
            html = self._build_html(result)
            delivery.save_draft(f"grants_{today}.html", html)

    def _build_html(self, result: dict) -> str:
        opportunities = result.get("opportunities", [])
        rows = ""
        for opp in opportunities:
            rel_color = {
                "high": "#cc0000",
                "medium": "#cc6600",
                "low": "#666666",
            }.get(opp.get("relevance", ""), "#333333")

            rows += f"""
            <tr>
                <td style="color:{rel_color};font-weight:bold;">{html.escape(opp.get('relevance', ''))}</td>
                <td><a href="{html.escape(opp['url'], quote=True)}">{html.escape(opp['title'])}</a></td>
                <td>{html.escape(opp['foa_number'])}</td>
                <td>{html.escape(opp['agency'])}</td>
                <td>{html.escape(opp['funding_range'])}</td>
                <td>{html.escape(opp['deadline'])}</td>
                <td>{html.escape(opp.get('one_liner', ''))}</td>
                <td><em>{html.escape(opp.get('fit_assessment', ''))}</em></td>
            </tr>"""

        return f"""<html><body>
        <h2>Grant Opportunities - {datetime.now().strftime('%Y-%m-%d')}</h2>
        <p>{html.escape(result.get('summary', ''))}</p>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
        <tr><th>Relevance</th><th>Title</th><th>FOA</th><th>Agency</th>
        <th>Funding</th><th>Deadline</th><th>Summary</th><th>Fit</th></tr>
        {rows}
        </table>
        </body></html>"""
