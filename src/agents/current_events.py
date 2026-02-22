"""Current events monitoring agent — topics loaded from config/current_events.yaml."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.agents.base import BaseAgent, load_current_events_config
from src.sources import rss, browser
from src import delivery

logger = logging.getLogger(__name__)


def _build_system_prompt(topics: dict) -> str:
    """Build the LLM system prompt dynamically from configured topics."""
    topic_keys = list(topics.keys())
    topic_names = ", ".join(t["label"] for t in topics.values())

    hint_lines = []
    for key, cfg in topics.items():
        hint = cfg.get("item_hint", "2-3 items")
        hint_lines.append(f"- {hint} for {cfg['label']}")
    hints_block = "\n".join(hint_lines)

    topic_enum = "|".join(topic_keys)

    return (
        f"You are a news briefing assistant. Below are numbered news items across "
        f"these topics: {topic_names}.\n\n"
        f"For each item, provide ONLY your analysis — do NOT repeat the title, source, or URL.\n\n"
        f"Return JSON with this exact structure:\n"
        f"{{\n"
        f'  "briefing": "3-4 sentence executive summary covering the most important developments across all topics",\n'
        f'  "selected_items": [\n'
        f"    {{\n"
        f'      "item_number": 1,\n'
        f'      "topic": "{topic_enum}",\n'
        f'      "one_liner": "Why this matters in one sentence",\n'
        f'      "importance": "high|medium|low"\n'
        f"    }}\n"
        f"  ]\n"
        f"}}\n\n"
        f"Select the most important items across all topics. Aim for a balanced mix:\n"
        f"{hints_block}\n\n"
        f"Skip clickbait, opinion pieces, and routine/repetitive items."
    )


class CurrentEventsAgent(BaseAgent):
    name = "current_events"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._ce_config = load_current_events_config()
        self._topics: dict[str, dict] = self._ce_config.get("topics", {})

    def fetch(self) -> list[dict]:
        all_items: list[dict] = []

        for topic_key, topic_cfg in self._topics.items():
            feed_list = topic_cfg.get("feeds", [])
            items = rss.fetch_multiple(feed_list, max_per_feed=8)
            for item in items:
                all_items.append({
                    "id": item.url or f"{item.source}:{item.title[:50]}",
                    "title": item.title,
                    "source": item.source,
                    "url": item.url,
                    "summary": item.summary,
                    "published": item.published,
                    "topic_hint": topic_key,
                })

        # Optionally scrape paywalled sites
        paywalled = self._ce_config.get("paywalled", [])
        if browser.is_available() and paywalled:
            for site in paywalled:
                if not site.get("enabled", False):
                    continue
                articles = browser.scrape_headlines(
                    site["url"],
                    source_name=site["name"],
                    max_items=5,
                    use_auth=True,
                )
                for a in articles:
                    all_items.append({
                        "id": a.url,
                        "title": a.title,
                        "source": a.source,
                        "url": a.url,
                        "summary": a.snippet,
                        "published": "",
                        "topic_hint": "",
                    })

        return all_items

    def dedup(self, items: list[dict], seen_ids: set[str]) -> list[dict]:
        return [item for item in items if item["id"] not in seen_ids]

    def extract_ids(self, items: list[dict]) -> list[str]:
        return [item["id"] for item in items]

    def summarize(self, items: list[dict]) -> dict[str, Any] | None:
        # Group by topic hint, take top items from each to ensure balance
        by_topic: dict[str, list[dict]] = {}
        for item in items:
            topic = item.get("topic_hint", "other")
            by_topic.setdefault(topic, []).append(item)

        # Build a balanced selection to send to LLM
        selected: list[dict] = []
        per_topic_limit = 8
        for topic_key in self._topics:
            selected.extend(by_topic.get(topic_key, [])[:per_topic_limit])
        # Add any uncategorized items
        selected.extend(by_topic.get("", [])[:5])

        # Build numbered list — no URLs sent to LLM
        content_parts = []
        for i, item in enumerate(selected, 1):
            topic_label = f" [{item.get('topic_hint', '')}]" if item.get("topic_hint") else ""
            content_parts.append(
                f"Item {i}{topic_label}:\n"
                f"Title: {item['title']}\n"
                f"Source: {item['source']}\n"
                f"Snippet: {item['summary'][:250]}\n"
            )
        content = "\n---\n".join(content_parts)
        if len(content) > 5500:
            content = content[:5500] + "\n[truncated]"

        system_prompt = _build_system_prompt(self._topics)
        llm_result = self._llm_summarize(system_prompt, content)
        if not llm_result:
            return None

        # Merge LLM selections with source data
        selections = {s["item_number"]: s for s in llm_result.get("selected_items", [])}
        merged_items = []
        for i, item in enumerate(selected, 1):
            if i in selections:
                s = selections[i]
                merged_items.append({
                    "title": item["title"],
                    "source": item["source"],
                    "url": item["url"],
                    "topic": s.get("topic", item.get("topic_hint", "")),
                    "one_liner": s.get("one_liner", ""),
                    "importance": s.get("importance", ""),
                })

        return {
            "briefing": llm_result.get("briefing", ""),
            "items": merged_items,
        }

    def deliver(self, result: dict[str, Any]) -> None:
        briefing = result.get("briefing", "No briefing available")
        items = result.get("items", [])
        count = len(items)
        today = datetime.now().strftime("%Y-%m-%d")
        time_label = datetime.now().strftime("%H:%M")

        if "text" in self.delivery_methods:
            delivery.send_text(briefing)

        if "email" in self.delivery_methods:
            html = self._build_html(result)
            delivery.send_email(f"Current Events Briefing - {today} {time_label}", html)

        if "notification" in self.delivery_methods:
            topics_count: dict[str, int] = {}
            for item in items:
                t = item.get("topic", "other")
                topics_count[t] = topics_count.get(t, 0) + 1
            topic_summary = ", ".join(f"{v} {k}" for k, v in topics_count.items())
            delivery.send_notification(
                "Current Events",
                f"{count} items: {topic_summary}",
            )

    def _build_html(self, result: dict) -> str:
        items = result.get("items", [])

        # Group by topic for organized display
        by_topic: dict[str, list[dict]] = {}
        for item in items:
            topic = item.get("topic", "other")
            by_topic.setdefault(topic, []).append(item)

        # Use config order for topic sections
        topic_order = list(self._topics.keys())

        sections_html = ""
        for topic_key in topic_order:
            topic_items = by_topic.get(topic_key, [])
            if not topic_items:
                continue
            cfg = self._topics[topic_key]
            color = cfg.get("color", "#333333")
            label = cfg.get("label", topic_key.title())

            rows = ""
            for item in topic_items:
                imp_color = {
                    "high": "#cc0000", "medium": "#cc6600", "low": "#999999"
                }.get(item.get("importance", ""), "#333333")
                rows += f"""
                <tr>
                    <td style="color:{imp_color};font-weight:bold;">{item.get('importance', '')}</td>
                    <td><a href="{item['url']}">{item['title']}</a></td>
                    <td>{item['source']}</td>
                    <td>{item.get('one_liner', '')}</td>
                </tr>"""

            sections_html += f"""
            <h3 style="color:{color};margin-top:20px;">{label}</h3>
            <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;">
            <tr><th>Importance</th><th>Title</th><th>Source</th><th>Summary</th></tr>
            {rows}
            </table>"""

        # Handle any uncategorized items
        other_items = by_topic.get("other", [])
        if other_items:
            rows = ""
            for item in other_items:
                rows += f"""
                <tr>
                    <td>{item.get('importance', '')}</td>
                    <td><a href="{item['url']}">{item['title']}</a></td>
                    <td>{item['source']}</td>
                    <td>{item.get('one_liner', '')}</td>
                </tr>"""
            sections_html += f"""
            <h3 style="margin-top:20px;">Other</h3>
            <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;">
            <tr><th>Importance</th><th>Title</th><th>Source</th><th>Summary</th></tr>
            {rows}
            </table>"""

        return f"""<html><body>
        <h2>Current Events Briefing - {datetime.now().strftime('%Y-%m-%d %H:%M')}</h2>
        <p style="font-size:16px;background:#f5f5f5;padding:12px;border-radius:6px;">
        {result.get('briefing', '')}</p>
        {sections_html}
        </body></html>"""
