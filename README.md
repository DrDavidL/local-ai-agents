# Local AI Agents

Scheduled AI agents and a Telegram chatbot running on a Mac, powered by **Ollama** (local LLM inference). Five agents monitor different information streams — literature, email, news, grants, and current events — produce concise summaries, and deliver them via text message, email, and macOS notifications. A Telegram bot provides mobile chat access to your local LLM.

All inference stays local. No cloud LLM API keys required.

## Agents

| Agent | What it does | Schedule | Sources |
|-------|-------------|----------|---------|
| **Literature** | Tracks new papers in clinical informatics and medical AI | Weekdays 6:30 AM | PubMed, arXiv, bioRxiv |
| **Email Triage** | Monitors a Gmail label, prioritizes emails, drafts replies | Every 2h, business hours | Gmail via Mail.app |
| **News** | Surfaces relevant healthcare AI and informatics news | 7 AM and 5 PM daily | RSS feeds (configurable) |
| **Grants** | Scans for NIH/NSF/federal funding opportunities | Mondays 7 AM | NIH RePORTER, Grants.gov |
| **Current Events** | Configurable news topics (e.g. world, local, markets, tech) | 7 AM, noon, 6 PM | RSS feeds (configurable) |

## Telegram Bot

Chat with your local Ollama from your phone via Telegram. Supports multi-turn conversations, configurable system prompt, and on-demand agent execution.

```bash
# Set up (one-time)
# 1. Message @BotFather on Telegram, send /newbot, copy the token
# 2. Add to .env:
#    TELEGRAM_BOT_TOKEN=your-token-here
#    TELEGRAM_ALLOWED_CHAT_IDS=your-chat-id

# 3. Copy and customize the system prompt
cp config/telegram.yaml.example config/telegram.yaml

# Run manually
uv run python scripts/telegram_bot.py

# Or install as a LaunchAgent (see Persistent Scheduling below)
```

**Bot commands:**

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/run <agent>` | Run an agent: `literature`, `email`, `news`, `grants`, `current`, `all` |
| `/search research <query>` | Search PubMed + arXiv for a topic |
| `/search news <query>` | Search Google News for a topic |
| `/med <drug>` | Look up a medication via FDA drug label |
| `/med <drug> interactions` | Focus on a specific section (interactions, dosage, warnings, etc.) |
| `/agents` | List available agents |
| `/clear` | Clear conversation history |
| `/model` | Show current LLM model |
| `/system <prompt>` | Change system prompt for this session |
| `/id` | Show your Telegram chat ID |

**Natural language triggers** — the bot detects intent and runs agents automatically:

| You say | What happens |
|---------|--------------|
| "Any new papers?" | Runs Literature agent |
| "Check my email" | Runs Email Triage agent |
| "What's in the news?" | Runs News agent |
| "Market update?" | Runs Current Events agent |
| "Any grants this week?" | Runs Grants agent |
| "Research fisetin" | Ad-hoc PubMed + arXiv search |
| "News - Russia" | Ad-hoc Google News search |
| "What is metformin?" | FDA medication lookup |
| "Lisinopril side effects" | Focused medication lookup |

All other messages go to the LLM for regular conversation.

## Architecture

```
Ollama (localhost:11434)
  ↕ OpenAI-compatible API
  ├─ Agents: literature │ email_triage │ news │ grants │ current_events
  │    ↓                        ↓
  │  Data Sources            LLM Summarization
  │  (PubMed, arXiv,             ↓
  │   RSS, Gmail,           Delivery
  │   NIH RePORTER)         ├─ Text (macOS Shortcuts Bridge)
  │                         ├─ Email (Gmail SMTP)
  │                         ├─ Notifications (macOS)
  │                         └─ File drafts (HTML/text)
  │
  └─ Telegram Bot ←→ Phone (long polling, no port forwarding needed)
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

# Run current events agent
uv run python scripts/run_agent.py current --dry-run
```

### Optional: Paywalled site access (WSJ, NYT)

If you have subscriptions to WSJ or NYT and want full-article extraction:

```bash
# Install Playwright
uv sync --extra scraping
playwright install chromium

# One-time: log into your subscriptions in the browser that opens
uv run python scripts/save_browser_auth.py

# Enable paywalled sources in config/current_events.yaml (set enabled: true)
```

## Configuration

All configuration lives in `config/`:

| File | Purpose |
|------|---------|
| `config/agents.yaml` | Master config: LLM model, schedules, delivery methods per agent |
| `config/feeds.yaml` | RSS feed URLs and PubMed search terms |
| `config/grant_sources.yaml` | NIH activity codes, keywords, grant search parameters |
| `config/current_events.yaml` | Personal current events topics and feeds (gitignored) |
| `config/current_events.yaml.example` | Template for current events config |
| `config/telegram.yaml` | Personal Telegram bot system prompt (gitignored) |
| `config/telegram.yaml.example` | Template for Telegram bot config |

### Current events topics

Copy the example and customize your topics:

```bash
cp config/current_events.yaml.example config/current_events.yaml
# Edit with your preferred topics, feeds, and colors
```

Each topic needs a key, label, color, item_hint, and a list of RSS feeds. See the example file for the full format.

### Telegram bot system prompt

```bash
cp config/telegram.yaml.example config/telegram.yaml
# Customize the system prompt for your bot's persona
```

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
| **Text** | macOS Shortcuts Bridge (configurable shortcut name) | Twilio SMS |
| **Email** | Gmail SMTP with HTML formatting | — |
| **Notification** | macOS Shortcuts Bridge | — |
| **Reminder** | macOS Shortcuts Bridge | — |
| **File draft** | Saved to `data/drafts/` as HTML/text | — |

## Persistent Scheduling

The project includes two long-running daemons, each with a LaunchAgent plist:

| Daemon | Plist | Purpose |
|--------|-------|---------|
| Scheduler | `com.local-ai-agents.scheduler.plist` | Runs all agents on cron schedules |
| Telegram Bot | `com.local-ai-agents.telegram-bot.plist` | Chat with Ollama from your phone |

To install either (or both):

```bash
# Edit the plist — replace YOURUSERNAME with your macOS username
vim com.local-ai-agents.scheduler.plist
vim com.local-ai-agents.telegram-bot.plist

# Install
cp com.local-ai-agents.scheduler.plist ~/Library/LaunchAgents/
cp com.local-ai-agents.telegram-bot.plist ~/Library/LaunchAgents/

# Load
launchctl load ~/Library/LaunchAgents/com.local-ai-agents.scheduler.plist
launchctl load ~/Library/LaunchAgents/com.local-ai-agents.telegram-bot.plist

# Check status
launchctl list | grep local-ai-agents

# View logs
tail -f data/scheduler.log
tail -f data/telegram-bot.log

# Restart (KeepAlive will relaunch automatically)
launchctl stop com.local-ai-agents.telegram-bot

# Unload
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
| `SHORTCUT_TEXT` | No | macOS Shortcut name for texting (default: `SendText`) |
| `SHORTCUT_NOTIFICATION` | No | macOS Shortcut name for notifications (default: `ShowNotification`) |
| `SHORTCUT_REMINDER` | No | macOS Shortcut name for reminders (default: `CreateReminder`) |
| `EMAIL_SENDER` | For email | Gmail address to send from |
| `EMAIL_RECIPIENT` | For email | Recipient email address |
| `GMAIL_APP_PASSWORD` | For email | Gmail app password ([create one](https://myaccount.google.com/apppasswords)) |
| `TELEGRAM_BOT_TOKEN` | For bot | Telegram bot token from @BotFather |
| `TELEGRAM_ALLOWED_CHAT_IDS` | For bot | Comma-separated allowed chat IDs |
| `TWILIO_ACCOUNT_SID` | No | Twilio fallback SMS |
| `TWILIO_AUTH_TOKEN` | No | Twilio fallback SMS |
| `TWILIO_FROM_NUMBER` | No | Twilio fallback SMS |
| `TWILIO_TO_NUMBER` | No | Twilio fallback SMS |

## Security

### Implemented safeguards

- **Fail-closed Telegram auth** -- the bot rejects all messages unless `TELEGRAM_ALLOWED_CHAT_IDS` is configured. Only `/id` works unauthenticated (for initial setup).
- **HTML escaping** -- all agent email digests escape RSS titles, URLs, and LLM output via `html.escape()` to prevent XSS injection through crafted feed content.
- **Safe XML parsing** -- PubMed and arXiv responses are parsed with `defusedxml` to prevent XML entity expansion attacks (billion laughs DoS).
- **Path traversal protection** -- `save_draft()` sanitizes filenames via `Path.name` to prevent `../` directory escape.
- **No token logging** -- httpx request logging is suppressed in the Telegram bot to prevent bot tokens from appearing in log files.
- **Restricted file permissions** -- `.env`, `data/`, and `browser_auth.json` are set to owner-only access (600/700).
- **Atomic history writes** -- `history.json` is written to a temp file and renamed to prevent corruption.
- **LLM/URL separation** -- URLs are never sent to the LLM; agents merge LLM analysis with source data to prevent URL hallucination.

### Post-install hardening

After cloning and configuring, restrict file permissions:

```bash
chmod 600 .env
chmod 700 data/
```

### Known limitations

- **Shortcuts Bridge uses HTTP** -- the auth token is sent over plain HTTP, which is acceptable for localhost-only traffic but not suitable if the bridge runs on a remote host.
- **No file locking on history.json** -- concurrent agent runs could race on read-modify-write. Mitigated by APScheduler running jobs sequentially by default.
- **RSS content in LLM prompts** -- crafted RSS titles could attempt prompt injection. Mitigated by using a local LLM (no exfiltration path) and the numbered-item pattern that separates LLM analysis from source data.

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
│   ├── agents.yaml                 # Schedules, delivery, LLM settings
│   ├── feeds.yaml                  # RSS feeds and search terms
│   ├── grant_sources.yaml          # Grant search parameters
│   ├── current_events.yaml.example # Template for current events topics
│   └── telegram.yaml.example       # Template for Telegram bot prompt
├── src/
│   ├── llm.py                      # Ollama client (OpenAI-compat)
│   ├── delivery.py                 # Text, email, notification, file delivery
│   ├── bridge_client.py            # Shortcuts Bridge HTTP client
│   ├── agents/
│   │   ├── base.py                 # BaseAgent ABC
│   │   ├── literature.py           # PubMed/arXiv/bioRxiv monitor
│   │   ├── email_triage.py         # Gmail label triage
│   │   ├── news.py                 # RSS news monitor
│   │   ├── grants.py               # NIH/NSF grant scanner
│   │   └── current_events.py       # Configurable topic news monitor
│   └── sources/
│       ├── pubmed.py               # PubMed E-utilities API
│       ├── arxiv.py                # arXiv Atom API
│       ├── biorxiv.py              # bioRxiv RSS
│       ├── rss.py                  # Generic RSS/Atom reader
│       ├── gmail.py                # Mail.app AppleScript reader
│       ├── nih_reporter.py         # NIH RePORTER API
│       ├── grants_gov.py           # Grants.gov RSS
│       ├── openfda.py              # FDA drug label API
│       └── browser.py              # Playwright scraper (optional)
├── scripts/
│   ├── run_agent.py                # CLI: run agents manually
│   ├── scheduler.py                # APScheduler daemon
│   ├── telegram_bot.py             # Telegram chat bot
│   └── save_browser_auth.py        # One-time WSJ/NYT login
├── tests/
├── data/                           # Runtime data (gitignored)
│   ├── history.json                # Dedup tracking
│   └── drafts/                     # Saved email drafts
├── com.local-ai-agents.scheduler.plist      # macOS LaunchAgent
└── com.local-ai-agents.telegram-bot.plist   # macOS LaunchAgent
```

## License

MIT
