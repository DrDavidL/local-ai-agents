"""Ollama LLM client via OpenAI-compatible API."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from openai import OpenAI, APIConnectionError, APITimeoutError

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def get_client(base_url: str = "http://localhost:11434/v1") -> OpenAI:
    """Get or create the OpenAI client pointed at Ollama."""
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=base_url,
            api_key="ollama",
            timeout=120.0,
        )
    return _client


def health_check(base_url: str = "http://localhost:11434") -> bool:
    """Check if Ollama is running and responsive."""
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


def summarize(
    system_prompt: str,
    content: str,
    *,
    model: str = "gemma3",
    max_tokens: int = 1024,
    temperature: float = 0.3,
    base_url: str = "http://localhost:11434/v1",
    max_retries: int = 2,
) -> str | None:
    """Send a summarization request to Ollama. Returns None on failure."""
    client = get_client(base_url)
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                max_completion_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content
        except (APIConnectionError, APITimeoutError) as exc:
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning("LLM call failed (attempt %d/%d), retrying in %ds: %s",
                               attempt + 1, max_retries + 1, wait, exc)
                time.sleep(wait)
            else:
                logger.error("LLM call failed after %d attempts: %s", max_retries + 1, exc)
                return None
        except Exception as exc:
            logger.error("Unexpected LLM error: %s", exc)
            return None
    return None


def structured_output(
    system_prompt: str,
    content: str,
    *,
    model: str = "gemma3",
    max_tokens: int = 2048,
    temperature: float = 0.2,
    base_url: str = "http://localhost:11434/v1",
    max_retries: int = 2,
) -> dict[str, Any] | None:
    """Request JSON output from the model. Returns None on failure."""
    client = get_client(base_url)
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt + "\n\nRespond with valid JSON only."},
                    {"role": "user", "content": content},
                ],
                max_completion_tokens=max_tokens,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            text = response.choices[0].message.content
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON: %s", exc)
            return None
        except (APIConnectionError, APITimeoutError) as exc:
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning("LLM call failed (attempt %d/%d), retrying in %ds: %s",
                               attempt + 1, max_retries + 1, wait, exc)
                time.sleep(wait)
            else:
                logger.error("LLM call failed after %d attempts: %s", max_retries + 1, exc)
                return None
        except Exception as exc:
            logger.error("Unexpected LLM error: %s", exc)
            return None
    return None
