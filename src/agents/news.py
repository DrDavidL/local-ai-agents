"""Medical + AI news monitoring agent."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.agents.base import BaseAgent, load_feeds_config
from src.sources import rss
from src import delivery

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a news curator for a physician-informaticist. Below are numbered news \
items. For each, provide ONLY your analysis — do NOT repeat the title, source, or URL.

Categorize each item: medical-ai, health-it, ai-general, policy, industry

Return JSON with this exact structure:
{
  "headline_summary": "2-3 sentence briefing of today's most important items",
  "selected_items": [
    {
      "item_number": 1,
      "category": "medical-ai",
      "one_liner": "Why this matters in one sentence",
      "relevance": "high|medium|low"
    }
  ]
}

Select at most 8 items. Prioritize actionable and novel information.
Skip routine product announcements unless from major health IT vendors."""


class NewsAgent(BaseAgent):
    name = "news"

    def fetch(self) -> list[dict]:
        feeds_config = load_feeds_config()
        news_config = feeds_config.get("news", {})
        feed_list = news_config.get("rss_feeds", [])

        items = rss.fetch_multiple(feed_list, max_per_feed=10)
        return [
            {
                "id": item.url or f"{item.source}:{item.title[:50]}",
                "title": item.title,
                "source": item.source,
                "url": item.url,
                "summary": item.summary,
                "published": item.published,
            }
            for item in items
        ]

    def dedup(self, items: list[dict], seen_ids: set[str]) -> list[dict]:
        return [item for item in items if item["id"] not in seen_ids]

    def extract_ids(self, items: list[dict]) -> list[str]:
        return [item["id"] for item in items]

    def summarize(self, items: list[dict]) -> dict[str, Any] | None:
        limited = items[:self.max_items * 3]

        # Build numbered list — no URLs sent to LLM
        content_parts = []
        for i, item in enumerate(limited, 1):
            content_parts.append(
                f"Item {i}:\n"
                f"Title: {item['title']}\n"
                f"Source: {item['source']}\n"
                f"Snippet: {item['summary'][:300]}\n"
            )
        content = "\n---\n".join(content_parts)
        if len(content) > 5000:
            content = content[:5000] + "\n[truncated]"

        llm_result = self._llm_summarize(SYSTEM_PROMPT, content)
        if not llm_result:
            return None

        # Merge LLM selections with source data
        selections = {s["item_number"]: s for s in llm_result.get("selected_items", [])}
        merged_items = []
        for i, item in enumerate(limited, 1):
            if i in selections:
                s = selections[i]
                merged_items.append({
                    "title": item["title"],
                    "source": item["source"],
                    "url": item["url"],
                    "category": s.get("category", ""),
                    "one_liner": s.get("one_liner", ""),
                    "relevance": s.get("relevance", ""),
                })

        return {
            "headline_summary": llm_result.get("headline_summary", ""),
            "items": merged_items,
        }

    def deliver(self, result: dict[str, Any]) -> None:
        headline = result.get("headline_summary", "No summary available")
        items = result.get("items", [])
        count = len(items)
        today = datetime.now().strftime("%Y-%m-%d")

        if "text" in self.delivery_methods:
            delivery.send_text(headline)

        if "email" in self.delivery_methods:
            html = self._build_html(result)
            delivery.send_email(f"News Digest - {today}", html)

        if "notification" in self.delivery_methods:
            delivery.send_notification(
                "News Monitor",
                f"{count} items: {headline[:100]}",
            )

    def _build_html(self, result: dict) -> str:
        items = result.get("items", [])
        rows = ""
        for item in items:
            category_color = {
                "medical-ai": "#0066cc",
                "health-it": "#009933",
                "ai-general": "#6600cc",
                "policy": "#cc6600",
                "industry": "#666666",
            }.get(item.get("category", ""), "#333333")

            rows += f"""
            <tr>
                <td style="color:{category_color};font-weight:bold;">{item.get('category', '')}</td>
                <td><a href="{item['url']}">{item['title']}</a></td>
                <td>{item['source']}</td>
                <td>{item.get('one_liner', '')}</td>
                <td>{item.get('relevance', '')}</td>
            </tr>"""

        return f"""<html><body>
        <h2>News Digest - {datetime.now().strftime('%Y-%m-%d %H:%M')}</h2>
        <p>{result.get('headline_summary', '')}</p>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
        <tr><th>Category</th><th>Title</th><th>Source</th><th>Summary</th><th>Relevance</th></tr>
        {rows}
        </table>
        </body></html>"""
