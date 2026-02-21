# Local AI Agents

Scheduled AI agents running on a Mac, powered by **Ollama** (local LLM inference). Four agents monitor different information streams — literature, email, news, and grants — produce concise summaries, and deliver them via text message, email, and macOS notifications.

All inference stays local. No cloud LLM API keys required.

## Agents

| Agent | What it does | Schedule | Sources |
|-------|-------------|----------|---------|
| **Literature** | Tracks new papers in clinical informatics and medical AI | Weekdays 6:30 AM | PubMed, arXiv, bioRxiv |
| **Email Triage** | Monitors a Gmail label, prioritizes emails, drafts replies | Every 2h, business hours | Gmail via Mail.app |
| **News** | Surfaces relevant healthcare AI and informatics news | 7 AM and 5 PM daily | RSS feeds (configurable) |
| **Grants** | Scans for NIH/NSF/federal funding opportunities | Mondays 7 AM | NIH RePORTER, Grants.gov |

## Architecture

```
Ollama (localhost:11434)
  ↕ OpenAI-compatible API
Agents: literature │ email_triage │ news │ grants
  ↓                        ↓
Data Sources            LLM Summarization
(PubMed, arXiv,             ↓
 RSS, Gmail,           Delivery
 NIH RePORTER)         ├─ Text (macOS Shortcuts Bridge)
                       ├─ Email (Gmail SMTP)
                       ├─ Notifications (macOS)
                       └─ File drafts (HTML/text)
```

## Prerequisites

- **macOS** (uses AppleScript for Gmail reading, Shortcuts for notifications)
- **[Ollama](https://ollama.com/)** with a model installed (e.g. `ollama pull gemma3`)
- **[uv](https://docs.astral.sh/uv/)** (Python package manager)
- **[Shortcuts Bridge](https://github.com/your-org/shortcuts-bridge)** (optional, for text/notification delivery)

## Quick Start

```bash
# Clone and install
git clone https://github.com/your-username/local-ai-agents.git
cd local-ai-agents
uv sync --all-extras

# Configure
cp .env.example .env
# Edit .env with your credentials (Gmail app password, etc.)

# Verify Ollama is running
uv run python scripts/run_agent.py literature --check-ollama

# Test a single agent (fetches real data, summarizes, but doesn't send)
uv run python scripts/run_agent.py literature --dry-run

# Run all agents in dry-run mode
uv run python scripts/run_agent.py all --dry-run
```

## Configuration

All configuration lives in `config/`:

| File | Purpose |
|------|---------|
| `config/agents.yaml` | Master config: LLM model, schedules, delivery methods per agent |
| `config/feeds.yaml` | RSS feed URLs and PubMed search terms |
| `config/grant_sources.yaml` | NIH activity codes, keywords, grant search parameters |

### Changing the LLM model

Edit `config/agents.yaml`:

```yaml
ollama:
  model: "gemma3"  # or any model from `ollama list`
```

### Adjusting schedules

Each agent has a cron expression in `config/agents.yaml`:

```yaml
agents:
  literature:
    schedule:
      cron: "30 6 * * 1-5"  # 6:30 AM weekdays
```

### Disabling an agent

```yaml
agents:
  grants:
    enabled: false
```

## Delivery

Agents deliver results through multiple channels (configurable per agent):

| Method | How | Fallback |
|--------|-----|----------|
| **Text** | macOS Shortcuts Bridge (`TextDavid` shortcut) | Twilio SMS |
| **Email** | Gmail SMTP with HTML formatting | — |
| **Notification** | macOS Shortcuts Bridge (`ShowNotification`) | — |
| **Reminder** | macOS Shortcuts Bridge (`CreateReminder`) | — |
| **File draft** | Saved to `data/drafts/` as HTML/text | — |

## Persistent Scheduling

The included APScheduler daemon runs all agents on their configured schedules. To run it as a macOS LaunchAgent (starts on login, restarts on crash):

```bash
# Edit the plist to set your username and paths
vim com.local-ai-agents.scheduler.plist

# Install
cp com.local-ai-agents.scheduler.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.local-ai-agents.scheduler.plist

# Check logs
tail -f data/scheduler.log

# Stop
launchctl unload ~/Library/LaunchAgents/com.local-ai-agents.scheduler.plist
```

## Environment Variables

Copy `.env.example` to `.env` and fill in your values:

| Variable | Required | Description |
|----------|----------|-------------|
| `OLLAMA_BASE_URL` | No | Ollama API URL (default: `http://localhost:11434/v1`) |
| `OLLAMA_MODEL` | No | Model name (default: from `config/agents.yaml`) |
| `PUBMED_API_KEY` | No | Free NCBI API key for higher rate limits ([register here](https://ncbiinsights.ncbi.nlm.nih.gov/2017/11/02/new-api-keys-for-the-e-utilities/)) |
| `SHORTCUTS_BRIDGE_URL` | No | Shortcuts Bridge URL (default: `http://localhost:9876`) |
| `SHORTCUTS_BRIDGE_TOKEN` | No | Auth token for Shortcuts Bridge |
| `EMAIL_SENDER` | For email | Gmail address to send from |
| `EMAIL_RECIPIENT` | For email | Recipient email address |
| `GMAIL_APP_PASSWORD` | For email | Gmail app password ([create one](https://myaccount.google.com/apppasswords)) |
| `TWILIO_ACCOUNT_SID` | No | Twilio fallback SMS |
| `TWILIO_AUTH_TOKEN` | No | Twilio fallback SMS |
| `TWILIO_FROM_NUMBER` | No | Twilio fallback SMS |
| `TWILIO_TO_NUMBER` | No | Twilio fallback SMS |

## Deduplication

Agents track previously seen items in `data/history.json` to avoid sending duplicate summaries. Each agent maintains its own ID list (PubMed PMIDs, arXiv IDs, DOIs, FOA numbers, etc.), capped at 5,000 entries.

## Testing

```bash
uv run pytest tests/ -v
```

## Project Structure

```
local-ai-agents/
├── config/
│   ├── agents.yaml          # Schedules, delivery, LLM settings
│   ├── feeds.yaml           # RSS feeds and search terms
│   └── grant_sources.yaml   # Grant search parameters
├── src/
│   ├── llm.py               # Ollama client (OpenAI-compat)
│   ├── delivery.py          # Text, email, notification, file delivery
│   ├── bridge_client.py     # Shortcuts Bridge HTTP client
│   ├── agents/
│   │   ├── base.py          # BaseAgent ABC
│   │   ├── literature.py    # PubMed/arXiv/bioRxiv monitor
│   │   ├── email_triage.py  # Gmail label triage
│   │   ├── news.py          # RSS news monitor
│   │   └── grants.py        # NIH/NSF grant scanner
│   └── sources/
│       ├── pubmed.py        # PubMed E-utilities API
│       ├── arxiv.py         # arXiv Atom API
│       ├── biorxiv.py       # bioRxiv RSS
│       ├── rss.py           # Generic RSS/Atom reader
│       ├── gmail.py         # Mail.app AppleScript reader
│       ├── nih_reporter.py  # NIH RePORTER API
│       └── grants_gov.py    # Grants.gov RSS
├── scripts/
│   ├── run_agent.py         # CLI: run agents manually
│   └── scheduler.py         # APScheduler daemon
├── tests/
├── data/                    # Runtime data (gitignored)
│   ├── history.json         # Dedup tracking
│   └── drafts/              # Saved email drafts
└── com.local-ai-agents.scheduler.plist  # macOS LaunchAgent
```

## License

MIT
