"""Read emails from Gmail label via Mail.app AppleScript."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Use the existing AppleScript from llm-actions
APPLESCRIPT_PATH = Path.home() / "GitHub" / "llm-actions" / "scripts" / "read_inbox_ai_safe.applescript"


@dataclass
class EmailMessage:
    from_addr: str
    subject: str
    date: str
    body: str
    attachments: str
    message_id: str


def read_label(label: str = "AI-REVIEW", max_items: int = 20) -> list[EmailMessage]:
    """Read emails from a Gmail label via Mail.app AppleScript.

    Uses the existing read_inbox_ai_safe.applescript, passing max_items as input.
    The script reads from a hardcoded label; for different labels, we'd need to
    modify the AppleScript or use a parameterized version.
    """
    if not APPLESCRIPT_PATH.exists():
        logger.error("AppleScript not found: %s", APPLESCRIPT_PATH)
        return []

    try:
        result = subprocess.run(
            ["osascript", str(APPLESCRIPT_PATH), str(max_items)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.error("AppleScript error: %s", result.stderr)
            return []

        output = result.stdout.strip()
        if not output or output == "[]":
            return []

        emails_data = json.loads(output)
        messages = []
        for item in emails_data:
            msg_id = f"{item.get('from', '')}:{item.get('subject', '')}:{item.get('date', '')}"
            messages.append(EmailMessage(
                from_addr=item.get("from", ""),
                subject=item.get("subject", ""),
                date=item.get("date", ""),
                body=item.get("body", "")[:3000],  # Truncate for LLM
                attachments=item.get("attachments", ""),
                message_id=msg_id,
            ))
        return messages
    except subprocess.TimeoutExpired:
        logger.error("AppleScript timed out reading Gmail")
        return []
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse AppleScript output as JSON: %s", exc)
        return []
    except Exception as exc:
        logger.error("Gmail read failed: %s", exc)
        return []
