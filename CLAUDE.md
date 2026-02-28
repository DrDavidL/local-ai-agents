# Local AI Agents

## Overview

Scheduled AI agents running on a home Mac via Ollama (local LLM). Four agents monitor literature, email, news, and grants, then deliver summaries via text, email, and notifications through the existing Shortcuts Bridge infrastructure. Model configurable in `config/agents.yaml` (currently `lfm2`).

## Architecture

```
Ollama (localhost:11434) ← OpenAI-compat API
    ↓
Agents: literature | email_triage | news | grants
    ↓                    ↓
Sources (PubMed,     LLM summarization
arXiv, RSS, Gmail,       ↓
NIH Reporter)        Delivery (Shortcuts Bridge text,
                     Gmail SMTP, file drafts, notifications)
```

## Key Files

| Location | Purpose |
|----------|---------|
| `src/llm.py` | Ollama client via OpenAI-compat API |
| `src/delivery.py` | Unified delivery: text, email, file, notification |
| `src/bridge_client.py` | Shortcuts Bridge HTTP client |
| `src/agents/base.py` | BaseAgent ABC + config/history helpers |
| `src/agents/*.py` | Individual agent implementations |
| `src/sources/*.py` | Data source clients (PubMed, arXiv, RSS, etc.) |
| `config/agents.yaml` | Master config: schedules, delivery prefs, Ollama settings |
| `scripts/run_agent.py` | CLI runner with `--dry-run` |
| `scripts/scheduler.py` | APScheduler daemon |

## Commands

```bash
# Install
uv sync --all-extras

# Run single agent
uv run python scripts/run_agent.py literature --dry-run

# Run all agents
uv run python scripts/run_agent.py all --dry-run

# Check Ollama health
uv run python scripts/run_agent.py literature --check-ollama

# Test
uv run pytest tests/ -v

# Start scheduler daemon
uv run python scripts/scheduler.py

# Install LaunchAgent (persistent scheduling)
cp com.local-ai-agents.scheduler.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.local-ai-agents.scheduler.plist
```

## Environment Variables

Store in `.env` (never commit). Copy from `.env.example`.

```bash
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=gemma3
SHORTCUTS_BRIDGE_URL=http://localhost:9876
SHORTCUTS_BRIDGE_TOKEN=<from shortcuts_bridge.local.json>
EMAIL_SENDER=your@gmail.com
EMAIL_RECIPIENT=your@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

## Key Rules

- All LLM calls go through local Ollama only — no cloud API keys for inference
- Keep prompts + content under 5000 chars (context safety margin)
- LLM calls timeout at 120s with 2 retries
- Dedup via `data/history.json` — never send the same item twice (cap: 5000 IDs per agent)
- If Ollama is down, skip gracefully (don't crash the scheduler)
- If Shortcuts Bridge is down, fall back to email-only

## Update Checklist

```bash
# After code changes:
uv run pytest tests/ -v
uv run python scripts/run_agent.py literature --dry-run  # smoke test
# Restart scheduler if config changed:
launchctl unload ~/Library/LaunchAgents/com.local-ai-agents.scheduler.plist
launchctl load ~/Library/LaunchAgents/com.local-ai-agents.scheduler.plist
```
