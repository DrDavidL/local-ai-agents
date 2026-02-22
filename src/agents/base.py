"""Base agent ABC: fetch -> summarize -> deliver."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src import llm, delivery

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
HISTORY_FILE = PROJECT_ROOT / "data" / "history.json"


def load_config() -> dict:
    """Load the master agents.yaml config."""
    config_path = CONFIG_DIR / "agents.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_feeds_config() -> dict:
    """Load feeds.yaml config."""
    config_path = CONFIG_DIR / "feeds.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_grants_config() -> dict:
    """Load grant_sources.yaml config."""
    config_path = CONFIG_DIR / "grant_sources.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_current_events_config() -> dict:
    """Load current_events.yaml config."""
    config_path = CONFIG_DIR / "current_events.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class HistoryStore:
    """Deduplication history with file locking."""

    def __init__(self, path: Path = HISTORY_FILE):
        self.path = path

    def load(self) -> dict:
        """Load history from disk."""
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load history, starting fresh: %s", exc)
            return {}

    def save(self, data: dict) -> None:
        """Save history to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.rename(self.path)  # Atomic on same filesystem

    def get_seen_ids(self, agent_name: str) -> set[str]:
        """Get the set of previously seen IDs for an agent."""
        data = self.load()
        agent_data = data.get(agent_name, {})
        return set(agent_data.get("seen_ids", []))

    def mark_seen(self, agent_name: str, new_ids: list[str]) -> None:
        """Mark IDs as seen for an agent."""
        data = self.load()
        if agent_name not in data:
            data[agent_name] = {"seen_ids": [], "last_run": None}
        existing = set(data[agent_name].get("seen_ids", []))
        existing.update(new_ids)
        # Keep only last 5000 IDs to prevent unbounded growth
        data[agent_name]["seen_ids"] = list(existing)[-5000:]
        data[agent_name]["last_run"] = datetime.now(timezone.utc).isoformat()
        self.save(data)


class BaseAgent(ABC):
    """Abstract base for all monitoring agents."""

    name: str = "base"

    def __init__(self, config: dict | None = None, dry_run: bool = False):
        self.config = config or load_config()
        self.dry_run = dry_run
        self.history = HistoryStore()
        self.ollama_config = self.config.get("ollama", {})
        self.delivery_config = self.config.get("delivery", {})
        agent_conf = self.config.get("agents", {}).get(self.name, {})
        self.agent_config = agent_conf
        self.max_items = agent_conf.get("max_items", 10)
        self.delivery_methods = agent_conf.get("delivery", [])

    def run(self) -> dict[str, Any] | None:
        """Execute the full agent pipeline: fetch -> dedup -> summarize -> deliver."""
        logger.info("Running agent: %s", self.name)

        # 1. Fetch raw data
        try:
            raw_items = self.fetch()
        except Exception as exc:
            logger.error("[%s] Fetch failed: %s", self.name, exc)
            return None

        if not raw_items:
            logger.info("[%s] No new items found", self.name)
            return None

        # 2. Dedup
        seen = self.history.get_seen_ids(self.name)
        new_items = self.dedup(raw_items, seen)
        if not new_items:
            logger.info("[%s] All items already seen", self.name)
            return None

        logger.info("[%s] %d new items after dedup", self.name, len(new_items))

        # 3. Summarize with LLM
        try:
            result = self.summarize(new_items)
        except Exception as exc:
            logger.error("[%s] Summarize failed: %s", self.name, exc)
            return None

        if result is None:
            logger.error("[%s] LLM returned no result", self.name)
            return None

        # 4. Mark as seen
        new_ids = self.extract_ids(new_items)
        self.history.mark_seen(self.name, new_ids)

        # 5. Deliver
        if not self.dry_run:
            self.deliver(result)
        else:
            logger.info("[%s] DRY RUN - would deliver:\n%s",
                        self.name, json.dumps(result, indent=2, default=str))

        return result

    @abstractmethod
    def fetch(self) -> list[Any]:
        """Fetch raw data from sources."""

    @abstractmethod
    def dedup(self, items: list[Any], seen_ids: set[str]) -> list[Any]:
        """Filter out previously seen items."""

    @abstractmethod
    def extract_ids(self, items: list[Any]) -> list[str]:
        """Extract unique IDs from items for dedup tracking."""

    @abstractmethod
    def summarize(self, items: list[Any]) -> dict[str, Any] | None:
        """Send items to LLM for summarization."""

    @abstractmethod
    def deliver(self, result: dict[str, Any]) -> None:
        """Send results via configured delivery methods."""

    def _llm_summarize(self, system_prompt: str, content: str) -> dict[str, Any] | None:
        """Helper: call LLM structured output with agent's config."""
        return llm.structured_output(
            system_prompt,
            content,
            model=self.ollama_config.get("model", "gemma3"),
            max_tokens=self.ollama_config.get("max_tokens", 2048),
            temperature=self.ollama_config.get("temperature", 0.2),
            base_url=self.ollama_config.get("base_url", "http://localhost:11434/v1"),
        )
