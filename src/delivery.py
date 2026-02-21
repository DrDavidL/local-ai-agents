"""Unified delivery layer: text, email, file drafts, notifications, reminders."""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from src.bridge_client import health as bridge_health, run_shortcut

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DRAFTS_DIR = PROJECT_ROOT / "data" / "drafts"


def send_text(message: str) -> bool:
    """Send via Shortcuts Bridge text shortcut. Falls back to Twilio if bridge is down."""
    shortcut = os.environ.get("SHORTCUT_TEXT", "SendText")
    if bridge_health():
        result = run_shortcut(shortcut, message)
        if result is not None:
            return True
        logger.warning("Shortcuts Bridge text failed, trying Twilio fallback")

    return _send_twilio_sms(message)


def send_email(subject: str, html_body: str) -> bool:
    """Send via Gmail SMTP."""
    sender = os.environ.get("EMAIL_SENDER", "")
    recipient = os.environ.get("EMAIL_RECIPIENT", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not all([sender, recipient, password]):
        logger.error("Email credentials not configured (EMAIL_SENDER, EMAIL_RECIPIENT, GMAIL_APP_PASSWORD)")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, [recipient], msg.as_string())
        logger.info("Email sent: %s", subject)
        return True
    except Exception as exc:
        logger.error("Email send failed: %s", exc)
        return False


def save_draft(filename: str, content: str) -> Path | None:
    """Save to data/drafts/. Returns the file path or None on failure."""
    try:
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        path = DRAFTS_DIR / filename
        path.write_text(content, encoding="utf-8")
        logger.info("Draft saved: %s", path)
        return path
    except Exception as exc:
        logger.error("Failed to save draft '%s': %s", filename, exc)
        return None


def send_notification(title: str, body: str) -> bool:
    """Send via Shortcuts Bridge notification shortcut."""
    shortcut = os.environ.get("SHORTCUT_NOTIFICATION", "ShowNotification")
    result = run_shortcut(shortcut, f"{title}\n{body}")
    if result is not None:
        return True
    logger.warning("Notification send failed")
    return False


def create_reminder(title: str, due_date: str | None = None) -> bool:
    """Create via Shortcuts Bridge reminder shortcut."""
    shortcut = os.environ.get("SHORTCUT_REMINDER", "CreateReminder")
    input_text = title
    if due_date:
        input_text = f"{title}\nDue: {due_date}"
    result = run_shortcut(shortcut, input_text)
    if result is not None:
        return True
    logger.warning("Reminder creation failed")
    return False


def _send_twilio_sms(message: str) -> bool:
    """Fallback SMS via Twilio."""
    try:
        from twilio.rest import Client
    except ImportError:
        logger.error("Twilio not installed (pip install twilio)")
        return False

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
    to_number = os.environ.get("TWILIO_TO_NUMBER", "")

    if not all([account_sid, auth_token, from_number, to_number]):
        logger.error("Twilio credentials not configured")
        return False

    try:
        client = Client(account_sid, auth_token)
        client.messages.create(body=message, from_=from_number, to=to_number)
        logger.info("Twilio SMS sent")
        return True
    except Exception as exc:
        logger.error("Twilio SMS failed: %s", exc)
        return False
