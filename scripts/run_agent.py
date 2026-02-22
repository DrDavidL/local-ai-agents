#!/usr/bin/env python3
"""CLI runner for local AI agents.

Usage:
    uv run python scripts/run_agent.py literature|email|news|grants|all [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from src.agents.base import load_config
from src.agents.literature import LiteratureAgent
from src.agents.email_triage import EmailTriageAgent
from src.agents.news import NewsAgent
from src.agents.grants import GrantsAgent
from src.agents.current_events import CurrentEventsAgent
from src import llm

load_dotenv()

AGENTS = {
    "literature": LiteratureAgent,
    "email": EmailTriageAgent,
    "news": NewsAgent,
    "grants": GrantsAgent,
    "current": CurrentEventsAgent,
}


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_agent(name: str, dry_run: bool = False) -> bool:
    """Run a single agent. Returns True on success."""
    if name not in AGENTS:
        logging.error("Unknown agent: %s (available: %s)", name, ", ".join(AGENTS))
        return False

    config = load_config()
    agent_class = AGENTS[name]
    agent = agent_class(config=config, dry_run=dry_run)

    if not agent.agent_config.get("enabled", True):
        logging.info("Agent '%s' is disabled in config", name)
        return True

    result = agent.run()
    if result is not None:
        logging.info("Agent '%s' completed successfully", name)
        return True
    else:
        logging.warning("Agent '%s' returned no results (may be normal if no new items)", name)
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local AI agents")
    parser.add_argument(
        "agent",
        choices=list(AGENTS.keys()) + ["all"],
        help="Which agent to run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and summarize but don't deliver",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--check-ollama",
        action="store_true",
        help="Only check if Ollama is running",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.check_ollama:
        ok = llm.health_check()
        print(f"Ollama: {'running' if ok else 'NOT RUNNING'}")
        sys.exit(0 if ok else 1)

    # Check Ollama before running agents
    if not llm.health_check():
        logging.error("Ollama is not running at localhost:11434. Start it first.")
        sys.exit(1)

    if args.agent == "all":
        for name in AGENTS:
            run_agent(name, dry_run=args.dry_run)
    else:
        run_agent(args.agent, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
