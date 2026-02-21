"""Literature monitoring agent: PubMed, arXiv, bioRxiv."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.agents.base import BaseAgent, load_feeds_config
from src.sources import pubmed, arxiv, biorxiv
from src import delivery

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a research assistant for an academic physician specializing in clinical \
informatics and AI in medicine.

Below are numbered papers. For each, provide ONLY your analysis — do NOT repeat the title, \
authors, source, or URL (those are already known).

Return JSON with this exact structure:
{
  "summary": "2-3 sentence overview of today's papers",
  "assessments": [
    {
      "item_number": 1,
      "one_liner": "One-sentence plain-English summary of what they found/built",
      "clinical_relevance": "high|medium|low",
      "tags": ["tag1", "tag2"]
    }
  ]
}

Select the 5 most relevant papers. Be concise."""


class LiteratureAgent(BaseAgent):
    name = "literature"

    def fetch(self) -> list[dict]:
        feeds_config = load_feeds_config()
        lit_config = feeds_config.get("literature", {})
        search_terms = lit_config.get("search_terms", [])
        categories = lit_config.get("arxiv_categories", ["cs.AI", "cs.CL", "cs.LG"])
        bio_subjects = lit_config.get("biorxiv_subjects", ["bioinformatics"])

        all_papers: list[dict] = []

        # PubMed
        for term in search_terms:
            articles = pubmed.search_and_fetch(term, max_results=10)
            for a in articles:
                all_papers.append({
                    "id": f"PMID:{a.pmid}",
                    "title": a.title,
                    "authors": a.authors,
                    "abstract": a.abstract,
                    "source": f"PubMed - {a.source}",
                    "url": a.url,
                    "date": a.pub_date,
                })

        # arXiv
        for term in search_terms[:2]:  # Limit to avoid rate limiting
            papers = arxiv.search(term, categories=categories, max_results=10)
            for p in papers:
                all_papers.append({
                    "id": f"arxiv:{p.arxiv_id}",
                    "title": p.title,
                    "authors": p.authors,
                    "abstract": p.abstract,
                    "source": "arXiv",
                    "url": p.url,
                    "date": p.published,
                })

        # bioRxiv
        for subject in bio_subjects:
            papers = biorxiv.fetch(subject=subject, max_results=10)
            for p in papers:
                all_papers.append({
                    "id": f"doi:{p.doi}" if p.doi else f"biorxiv:{p.title[:50]}",
                    "title": p.title,
                    "authors": p.authors,
                    "abstract": p.abstract,
                    "source": "bioRxiv",
                    "url": p.url,
                    "date": p.published,
                })

        return all_papers

    def dedup(self, items: list[dict], seen_ids: set[str]) -> list[dict]:
        return [item for item in items if item["id"] not in seen_ids]

    def extract_ids(self, items: list[dict]) -> list[str]:
        return [item["id"] for item in items]

    def summarize(self, items: list[dict]) -> dict[str, Any] | None:
        # Send more than we need; LLM picks the best
        limited = items[:self.max_items * 2]

        # Build numbered list for LLM (no URLs — just content for analysis)
        content_parts = []
        for i, item in enumerate(limited, 1):
            content_parts.append(
                f"Item {i}:\n"
                f"Title: {item['title']}\n"
                f"Authors: {item['authors']}\n"
                f"Source: {item['source']}\n"
                f"Abstract: {item['abstract'][:500]}\n"
            )
        content = "\n---\n".join(content_parts)
        if len(content) > 5000:
            content = content[:5000] + "\n[truncated]"

        llm_result = self._llm_summarize(SYSTEM_PROMPT, content)
        if not llm_result:
            return None

        # Merge LLM assessments with source data (which has real URLs)
        assessments = {a["item_number"]: a for a in llm_result.get("assessments", [])}
        merged_papers = []
        for i, item in enumerate(limited, 1):
            if i in assessments:
                a = assessments[i]
                merged_papers.append({
                    "title": item["title"],
                    "authors": item["authors"],
                    "source": item["source"],
                    "url": item["url"],
                    "one_liner": a.get("one_liner", ""),
                    "clinical_relevance": a.get("clinical_relevance", ""),
                    "tags": a.get("tags", []),
                })

        return {
            "summary": llm_result.get("summary", ""),
            "papers": merged_papers,
        }

    def deliver(self, result: dict[str, Any]) -> None:
        summary = result.get("summary", "No summary available")
        papers = result.get("papers", [])
        count = len(papers)

        if "text" in self.delivery_methods:
            msg = f"{summary}\n\n{count} papers found."
            delivery.send_text(msg)

        if "email" in self.delivery_methods:
            html = self._build_html(result)
            delivery.send_email(
                f"Literature Digest - {datetime.now().strftime('%Y-%m-%d')}",
                html,
            )

        if "file" in self.delivery_methods or "email" in self.delivery_methods:
            html = self._build_html(result)
            delivery.save_draft(
                f"literature_{datetime.now().strftime('%Y-%m-%d')}.html",
                html,
            )

        if "notification" in self.delivery_methods:
            delivery.send_notification(
                "Literature Monitor",
                f"{count} new papers in clinical AI",
            )

    def _build_html(self, result: dict) -> str:
        papers = result.get("papers", [])
        rows = ""
        for p in papers:
            tags = ", ".join(p.get("tags", [])) if isinstance(p.get("tags"), list) else p.get("tags", "")
            rows += f"""
            <tr>
                <td><a href="{p['url']}">{p['title']}</a></td>
                <td>{p['authors']}</td>
                <td>{p['source']}</td>
                <td>{p.get('one_liner', '')}</td>
                <td>{p.get('clinical_relevance', '')}</td>
                <td>{tags}</td>
            </tr>"""

        return f"""<html><body>
        <h2>Literature Digest - {datetime.now().strftime('%Y-%m-%d')}</h2>
        <p>{result.get('summary', '')}</p>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
        <tr><th>Title</th><th>Authors</th><th>Source</th><th>Summary</th><th>Relevance</th><th>Tags</th></tr>
        {rows}
        </table>
        </body></html>"""
