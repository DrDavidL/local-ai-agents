"""Email triage agent: Gmail label monitor."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.agents.base import BaseAgent
from src.sources import gmail
from src import delivery

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an executive assistant for an academic physician. \
Below are numbered emails. For each, provide ONLY your analysis — do NOT repeat \
the from address, subject, or date (those are already known).

Return JSON with this exact structure:
{
  "summary": "Brief overview of the inbox state",
  "urgent_count": 0,
  "assessments": [
    {
      "item_number": 1,
      "priority": "urgent|action-needed|fyi",
      "category": "meeting|request|review|deadline|informational",
      "one_liner": "What this email is about",
      "next_action": "What to do next",
      "draft_reply": "2-3 sentence professional reply, or empty string if no reply needed"
    }
  ]
}"""


class EmailTriageAgent(BaseAgent):
    name = "email_triage"

    def fetch(self) -> list[dict]:
        label = self.agent_config.get("gmail_label", "AI-REVIEW")
        messages = gmail.read_label(label=label, max_items=self.max_items)
        return [
            {
                "id": msg.message_id,
                "from": msg.from_addr,
                "subject": msg.subject,
                "date": msg.date,
                "body": msg.body,
                "attachments": msg.attachments,
            }
            for msg in messages
        ]

    def dedup(self, items: list[dict], seen_ids: set[str]) -> list[dict]:
        return [item for item in items if item["id"] not in seen_ids]

    def extract_ids(self, items: list[dict]) -> list[str]:
        return [item["id"] for item in items]

    def summarize(self, items: list[dict]) -> dict[str, Any] | None:
        limited = items[:self.max_items]

        # Build numbered list for LLM
        content_parts = []
        for i, item in enumerate(limited, 1):
            content_parts.append(
                f"Item {i}:\n"
                f"From: {item['from']}\n"
                f"Subject: {item['subject']}\n"
                f"Date: {item['date']}\n"
                f"Body: {item['body'][:1000]}\n"
                f"Attachments: {item['attachments']}\n"
            )
        content = "\n---\n".join(content_parts)
        if len(content) > 5000:
            content = content[:5000] + "\n[truncated]"

        llm_result = self._llm_summarize(SYSTEM_PROMPT, content)
        if not llm_result:
            return None

        # Merge LLM assessments with source data
        assessments = {a["item_number"]: a for a in llm_result.get("assessments", [])}
        merged_items = []
        for i, item in enumerate(limited, 1):
            if i in assessments:
                a = assessments[i]
                merged_items.append({
                    "from": item["from"],
                    "subject": item["subject"],
                    "date": item["date"],
                    "priority": a.get("priority", "fyi"),
                    "category": a.get("category", ""),
                    "one_liner": a.get("one_liner", ""),
                    "next_action": a.get("next_action", ""),
                    "draft_reply": a.get("draft_reply", ""),
                })

        return {
            "summary": llm_result.get("summary", ""),
            "urgent_count": llm_result.get("urgent_count", 0),
            "items": merged_items,
        }

    def deliver(self, result: dict[str, Any]) -> None:
        summary = result.get("summary", "")
        urgent_count = result.get("urgent_count", 0)
        items = result.get("items", [])
        today = datetime.now().strftime("%Y-%m-%d")

        if "text" in self.delivery_methods and urgent_count > 0:
            urgent_subjects = [
                item["subject"]
                for item in items
                if item.get("priority") == "urgent"
            ]
            msg = f"{urgent_count} urgent emails in AI-REVIEW: {', '.join(urgent_subjects[:3])}"
            delivery.send_text(msg)

        if "email" in self.delivery_methods:
            html = self._build_html(result)
            delivery.send_email(f"Email Triage - {today}", html)

        if "file" in self.delivery_methods:
            for item in items:
                if item.get("draft_reply"):
                    safe_subject = "".join(
                        c if c.isalnum() or c in " -_" else "_"
                        for c in item.get("subject", "unknown")
                    )[:50]
                    delivery.save_draft(
                        f"reply_{safe_subject}_{today}.txt",
                        f"To: {item['from']}\n"
                        f"Subject: Re: {item['subject']}\n\n"
                        f"{item['draft_reply']}",
                    )

        if "reminder" in self.delivery_methods:
            for item in items:
                if item.get("priority") in ("urgent", "action-needed"):
                    delivery.create_reminder(
                        f"Email: {item['subject']} - {item.get('next_action', 'Review')}",
                    )

    def _build_html(self, result: dict) -> str:
        items = result.get("items", [])
        rows = ""
        for item in items:
            priority_color = {
                "urgent": "#ff4444",
                "action-needed": "#ff8800",
                "fyi": "#44aa44",
            }.get(item.get("priority", ""), "#888888")

            rows += f"""
            <tr>
                <td style="color:{priority_color};font-weight:bold;">{item.get('priority', '')}</td>
                <td>{item['from']}</td>
                <td>{item['subject']}</td>
                <td>{item.get('category', '')}</td>
                <td>{item.get('one_liner', '')}</td>
                <td>{item.get('next_action', '')}</td>
                <td><em>{item.get('draft_reply', '')}</em></td>
            </tr>"""

        return f"""<html><body>
        <h2>Email Triage - {datetime.now().strftime('%Y-%m-%d %H:%M')}</h2>
        <p>{result.get('summary', '')}</p>
        <p><strong>Urgent items:</strong> {result.get('urgent_count', 0)}</p>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
        <tr><th>Priority</th><th>From</th><th>Subject</th><th>Category</th>
        <th>Summary</th><th>Action</th><th>Draft Reply</th></tr>
        {rows}
        </table>
        </body></html>"""
