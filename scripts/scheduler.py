#!/usr/bin/env python3
"""APScheduler daemon for running agents on cron schedules."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.agents.base import load_config
from src.agents.literature import LiteratureAgent
from src.agents.email_triage import EmailTriageAgent
from src.agents.news import NewsAgent
from src.agents.grants import GrantsAgent
from src import llm

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

AGENT_CLASSES = {
    "literature": LiteratureAgent,
    "email_triage": EmailTriageAgent,
    "news": NewsAgent,
    "grants": GrantsAgent,
}


def run_agent_job(agent_name: str) -> None:
    """Callback for scheduled agent execution."""
    logger.info("Scheduled run: %s", agent_name)
    try:
        if not llm.health_check():
            logger.error("Ollama not available, skipping %s", agent_name)
            return

        config = load_config()
        agent_class = AGENT_CLASSES[agent_name]
        agent = agent_class(config=config)
        agent.run()
    except Exception as exc:
        logger.error("Agent '%s' failed: %s", agent_name, exc, exc_info=True)


def parse_cron(cron_expr: str) -> dict:
    """Parse a 5-field cron expression into APScheduler kwargs."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {cron_expr}")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def main() -> None:
    config = load_config()
    agents_config = config.get("agents", {})

    scheduler = BlockingScheduler(timezone="America/Chicago")

    for agent_name, agent_conf in agents_config.items():
        if not agent_conf.get("enabled", True):
            logger.info("Agent '%s' is disabled, skipping", agent_name)
            continue

        cron_expr = agent_conf.get("schedule", {}).get("cron")
        if not cron_expr:
            logger.warning("No cron schedule for agent '%s', skipping", agent_name)
            continue

        if agent_name not in AGENT_CLASSES:
            logger.warning("Unknown agent '%s', skipping", agent_name)
            continue

        try:
            cron_kwargs = parse_cron(cron_expr)
            trigger = CronTrigger(**cron_kwargs, timezone="America/Chicago")
            scheduler.add_job(
                run_agent_job,
                trigger=trigger,
                args=[agent_name],
                id=agent_name,
                name=f"Agent: {agent_name}",
                misfire_grace_time=300,
            )
            logger.info("Scheduled '%s': %s", agent_name, cron_expr)
        except Exception as exc:
            logger.error("Failed to schedule '%s': %s", agent_name, exc)

    logger.info("Scheduler starting with %d jobs", len(scheduler.get_jobs()))

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
