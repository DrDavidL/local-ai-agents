"""Shortcuts Bridge HTTP client."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:9876"
DEFAULT_TIMEOUT = 30.0


def _get_url() -> str:
    return os.environ.get("SHORTCUTS_BRIDGE_URL", DEFAULT_URL)


def _get_token() -> str:
    return os.environ.get("SHORTCUTS_BRIDGE_TOKEN", "")


def run_shortcut(shortcut: str, input_text: str = "") -> dict | None:
    """Run a macOS shortcut via the Shortcuts Bridge.

    Returns the response dict on success, None on failure.
    """
    url = f"{_get_url()}/run"
    token = _get_token()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Bridge-Token"] = token

    try:
        resp = httpx.post(
            url,
            json={"shortcut": shortcut, "input": input_text},
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("success"):
            logger.info("Shortcut '%s' executed successfully", shortcut)
            return data
        logger.warning("Shortcut '%s' failed: %s", shortcut, data.get("error", "unknown"))
        return None
    except httpx.TimeoutException:
        logger.error("Shortcuts Bridge timed out for '%s'", shortcut)
        return None
    except httpx.HTTPError as exc:
        logger.error("Shortcuts Bridge HTTP error for '%s': %s", shortcut, exc)
        return None
    except Exception as exc:
        logger.error("Shortcuts Bridge unexpected error for '%s': %s", shortcut, exc)
        return None


def health() -> bool:
    """Check if the Shortcuts Bridge is running."""
    try:
        resp = httpx.get(f"{_get_url()}/health", timeout=5.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
