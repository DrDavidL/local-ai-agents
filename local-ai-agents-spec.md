# Local AI Agents - Build Specification

> Use this document with Claude Code to build a new project: `~/GitHub/local-ai-agents/`

## What This Is

A suite of scheduled AI agents running on a home Mac, powered by **Ollama + Gemma 3** via the OpenAI-compatible local API. Each agent monitors a different information stream (literature, email, news, grants), produces concise summaries, and delivers them via text, email drafts, and notifications using the existing macOS Shortcuts Bridge infrastructure.

## Existing Infrastructure to Reuse

These are already running on this Mac. Wire into them -- do not rebuild.

| System | Location | What It Provides |
|--------|----------|-----------------|
| **Shortcuts Bridge** | `shortcuts_bridge.py` on `localhost:9876` | Send texts (TextDavid), emails (EmailDavid), reminders (CreateReminder), notifications (ShowNotification) via macOS Shortcuts |
| **Gmail SMTP** | Gmail SMTP pattern | HTML email reports via `smtp.gmail.com:587` with `GMAIL_APP_PASSWORD` |
| **Twilio SMS** | Twilio SMS pattern | SMS alerts via Twilio API (fallback if Shortcuts Bridge is down) |
| **LaunchAgents** | `~/Library/LaunchAgents/` | macOS scheduling via plist files |
| **AppleScript Gmail reader** | `read_inbox_ai_safe.applescript` | Read emails from specific Gmail labels via Mail.app |

## Architecture

```
                     +-----------------+
                     |   Ollama        |
                     |   (Gemma 3)     |
                     |   localhost:11434|
                     +--------+--------+
                              |  OpenAI-compat API
                              |
              +---------------+---------------+
              |               |               |
    +---------+--+  +---------+--+  +---------+--+  +---------+--+
    | Literature  |  | Email      |  | News        |  | Grants     |
    | Agent       |  | Agent      |  | Agent       |  | Agent      |
    +------+------+  +------+------+  +------+------+  +------+------+
           |                |                |                |
           v                v                v                v
    +------+------+  +------+------+  +------+------+  +------+------+
    | PubMed      |  | Mail.app    |  | RSS feeds   |  | NIH Reporter|
    | arXiv       |  | (Gmail      |  | NewsAPI     |  | Grants.gov  |
    | bioRxiv     |  |  label)     |  | PubMed News |  | NSF         |
    +--------------+  +--------------+  +--------------+  +--------------+
              |               |               |                |
              +-------+-------+-------+-------+
                      |
              +-------v--------+
              | Delivery Layer |
              | - Shortcuts    |
              |   Bridge (text)|
              | - Gmail SMTP   |
              |   (email)      |
              | - File drafts  |
              |   (~/Desktop)  |
              +----------------+
```

## Project Structure

```
~/GitHub/local-ai-agents/
  pyproject.toml              # uv project, deps: httpx, pyyaml, feedparser, python-dotenv
  .env                        # Secrets (GMAIL_APP_PASSWORD, TWILIO_*, SHORTCUTS_BRIDGE_TOKEN, etc.)
  config/
    agents.yaml               # Master config: schedules, sources, delivery prefs per agent
    feeds.yaml                # RSS feed URLs for news + literature
    grant_sources.yaml        # Grant search terms, NIH activity codes, NSF directorates
  src/
    __init__.py
    llm.py                    # Ollama client via OpenAI-compat API (localhost:11434/v1)
    delivery.py               # Unified notification: text, email, file draft, notification
    bridge_client.py          # Shortcuts Bridge HTTP client (port from llm-actions)
    agents/
      __init__.py
      base.py                 # BaseAgent ABC: fetch -> summarize -> deliver
      literature.py           # PubMed/arXiv/bioRxiv agent
      email_triage.py         # Gmail label monitor agent
      news.py                 # Medical + AI news agent
      grants.py               # Grant opportunity scanner
    sources/
      __init__.py
      pubmed.py               # PubMed E-utilities API (free, no key needed for <3 req/sec)
      arxiv.py                # arXiv API (Atom feed)
      biorxiv.py              # bioRxiv RSS
      rss.py                  # Generic RSS/Atom feed reader
      gmail.py                # Read from Gmail label via AppleScript + Mail.app
      nih_reporter.py         # NIH RePORTER API (free, no key)
      grants_gov.py           # Grants.gov RSS feed
  scripts/
    run_agent.py              # CLI: python scripts/run_agent.py literature|email|news|grants|all
    scheduler.py              # APScheduler daemon (same pattern as taxable-portfolio)
  data/
    history.json              # Dedup: track seen article IDs, grant numbers, email IDs
    drafts/                   # Email draft files saved here
  tests/
    test_llm.py
    test_agents.py
    test_sources.py
```

## LLM Integration: Ollama via OpenAI-Compatible API

Use the `openai` Python library pointed at the local Ollama server. This is the core pattern for `src/llm.py`:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",  # Ollama ignores this but the library requires it
)

def summarize(system_prompt: str, content: str, max_tokens: int = 1024) -> str:
    response = client.chat.completions.create(
        model="gemma3",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        max_completion_tokens=max_tokens,
        temperature=0.3,
    )
    return response.choices[0].message.content

def structured_output(system_prompt: str, content: str, max_tokens: int = 2048) -> dict:
    """Request JSON output from the model."""
    response = client.chat.completions.create(
        model="gemma3",
        messages=[
            {"role": "system", "content": system_prompt + "\n\nRespond with valid JSON only."},
            {"role": "user", "content": content},
        ],
        max_completion_tokens=max_tokens,
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)
```

**Important:** Gemma 3 via Ollama supports the `/v1/chat/completions` endpoint. Test with:
```bash
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma3","messages":[{"role":"user","content":"hello"}]}'
```

## Agent Specifications

### Agent 1: Literature Monitor

**Purpose:** Track new papers in clinical informatics, medical AI, and LLM applications in healthcare.

**Sources:**
- PubMed E-utilities API (`eutils.ncbi.nlm.nih.gov`) -- search recent publications
- arXiv API (`export.arxiv.org/api/query`) -- cs.AI, cs.CL, cs.LG categories
- bioRxiv RSS (`connect.biorxiv.org/biorxiv_xml.php?subject=bioinformatics`)

**Search terms (configurable in `config/feeds.yaml`):**
- `"clinical informatics" OR "clinical decision support" OR "medical AI"`
- `"large language model" AND ("medicine" OR "healthcare" OR "clinical")`
- `"machine learning" AND ("EHR" OR "electronic health record")`
- `"AI" AND ("diagnosis" OR "radiology" OR "pathology")`

**Schedule:** Daily at 6:30 AM (before morning routine)

**LLM prompt pattern:**
```
You are a research assistant for an academic physician specializing in clinical
informatics and AI in medicine. Summarize the following new papers.

For each paper, provide:
- One-sentence plain-English summary of what they found/built
- Clinical relevance (high/medium/low)
- Whether it relates to ongoing work in LLMs, clinical decision support, or EHR integration

Return JSON: {"summary": "2-3 sentence overview", "papers": [{"title", "authors_short",
"source", "url", "one_liner", "clinical_relevance", "tags"}]}

Limit to the 5 most relevant papers. Be concise.
```

**Delivery:**
- Text (via Shortcuts Bridge `TextDavid`): 2-3 sentence summary + count of papers
- Email draft: Full HTML digest with paper details, saved to `data/drafts/literature_YYYY-MM-DD.html`
- macOS notification: "5 new papers in clinical AI" (via `ShowNotification`)

**Dedup:** Track PubMed PMIDs, arXiv IDs, bioRxiv DOIs in `data/history.json`

---

### Agent 2: Email Triage

**Purpose:** Monitor a specific Gmail label (e.g., `AI-REVIEW`) for emails that need attention, summarize and prioritize them.

**Source:**
- Gmail via Mail.app AppleScript (same pattern as `read_inbox_ai_safe.applescript`)
- Target label/mailbox: configurable, default `AI-REVIEW`

**Schedule:** Every 2 hours during business hours (8 AM - 6 PM, Mon-Fri)

**LLM prompt pattern:**
```
You are an executive assistant for an academic physician. Triage these emails by urgency and draft concise responses.

For each email:
- Priority: urgent / action-needed / fyi
- Category: meeting / request / review / deadline / informational
- One-line summary
- Suggested next action
- Draft reply (2-3 sentences, professional tone) if reply needed

Return JSON: {"summary": "...", "urgent_count": N, "items": [{"from", "subject",
"priority", "category", "one_liner", "next_action", "draft_reply"}]}
```

**Delivery:**
- Text (if urgent items): "3 urgent emails in AI-REVIEW: [subjects]"
- Email: HTML digest with all items, draft replies included
- File: Draft replies saved individually to `data/drafts/reply_SUBJECT_DATE.txt`
- Reminders (via Shortcuts Bridge `CreateReminder`): for deadline/action-needed items

---

### Agent 3: Medical + AI News Monitor

**Purpose:** Surface relevant news in healthcare AI, clinical informatics, and general AI developments.

**Sources (configurable RSS feeds in `config/feeds.yaml`):**
- STAT News (health/tech)
- Healthcare IT News RSS
- The Batch (deeplearning.ai newsletter)
- MIT Technology Review AI feed
- Nature Medicine news
- JAMIA (Journal of AMIA) new articles
- Hacker News (filtered for AI/medical keywords)

**Schedule:** Twice daily -- 7:00 AM and 5:00 PM

**LLM prompt pattern:**
```
You are a news curator for a physician-informaticist. Filter and summarize
today's news in medical AI, clinical informatics, health IT policy, and
significant general AI developments.

Categorize each item:
- medical-ai, health-it, ai-general, policy, industry

Return JSON: {"headline_summary": "2-3 sentence briefing", "items": [{"title",
"source", "url", "category", "one_liner", "relevance"}]}

Include at most 8 items. Prioritize actionable and novel information.
Skip routine product announcements unless from major health IT vendors.
```

**Delivery:**
- Text: 2-sentence headline briefing
- Email: HTML news digest with links
- macOS notification: headline count

---

### Agent 4: Grant Opportunity Scanner

**Purpose:** Find relevant NIH, NSF, and other federal grant opportunities in clinical informatics and AI.

**Sources:**
- NIH RePORTER API (`api.reporter.nih.gov`) -- new FOAs (Funding Opportunity Announcements)
- Grants.gov RSS/API -- search for relevant opportunities
- NSF awards search (filtered by directorate)

**Search terms (configurable in `config/grant_sources.yaml`):**
- Activity codes: R01, R21, R03, U01, K23, K08, T32, P30
- Keywords: `"clinical informatics"`, `"artificial intelligence" AND "health"`, `"machine learning" AND "clinical"`, `"decision support"`, `"natural language processing" AND "medicine"`
- IC codes: NLM, NIBIB, NCI, NHLBI, NIGMS (configurable)

**Schedule:** Weekly on Monday at 7:00 AM

**LLM prompt pattern:**
```
You are a grants specialist for an academic physician-informaticist. Review
these funding opportunities and assess fit.

For each opportunity:
- Relevance: high/medium/low (to clinical informatics + AI research)
- Deadline
- Funding range if available
- One-line description of what they're funding
- Fit assessment: why this matches or doesn't match the PI's profile

Return JSON: {"summary": "...", "opportunities": [{"title", "foa_number", "agency",
"deadline", "funding_range", "relevance", "one_liner", "fit_assessment", "url"}]}

Only include medium and high relevance opportunities.
```

**Delivery:**
- Text: "3 new grant opportunities this week, 1 high relevance (deadline Mar 15)"
- Email: Detailed HTML report with full opportunity details
- File: `data/drafts/grants_YYYY-MM-DD.html`

**Dedup:** Track FOA numbers in `data/history.json`

---

## Configuration: `config/agents.yaml`

```yaml
ollama:
  base_url: "http://localhost:11434/v1"
  model: "gemma3"
  temperature: 0.3
  max_tokens: 2048

delivery:
  shortcuts_bridge:
    url: "http://localhost:9876"
    # Token loaded from SHORTCUTS_BRIDGE_TOKEN env var
  email:
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    # Credentials from EMAIL_SENDER, EMAIL_RECIPIENT, GMAIL_APP_PASSWORD env vars
  drafts_dir: "data/drafts"
  text_shortcut: "TextDavid"
  email_shortcut: "EmailDavid"
  notification_shortcut: "ShowNotification"
  reminder_shortcut: "CreateReminder"

agents:
  literature:
    enabled: true
    schedule:
      cron: "30 6 * * 1-5"  # 6:30 AM weekdays
    delivery: [text, email, notification]
    max_items: 5

  email_triage:
    enabled: true
    gmail_label: "AI-REVIEW"
    schedule:
      cron: "0 8-18/2 * * 1-5"  # Every 2 hours, 8AM-6PM weekdays
    delivery: [text, email, reminder, file]
    max_items: 20

  news:
    enabled: true
    schedule:
      cron: "0 7,17 * * *"  # 7 AM and 5 PM daily
    delivery: [text, email, notification]
    max_items: 8

  grants:
    enabled: true
    schedule:
      cron: "0 7 * * 1"  # Monday 7 AM
    delivery: [text, email, file]
    max_items: 10
```

## Scheduling: APScheduler Daemon

APScheduler blocking scheduler pattern:

```python
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BlockingScheduler(timezone="America/Chicago")
# Add jobs from config/agents.yaml cron expressions
scheduler.start()
```

LaunchAgent plist at `~/Library/LaunchAgents/com.local-ai-agents.scheduler.plist`:
- `RunAtLoad: true`, `KeepAlive: true`
- Uses `/opt/homebrew/bin/uv run python -u scripts/scheduler.py`
- Logs to `data/scheduler.log` and `data/scheduler.err`
- PATH includes `/opt/homebrew/bin`

## Delivery Layer: `src/delivery.py`

Unified delivery interface. Each method is fire-and-forget with graceful fallback:

```python
def send_text(message: str) -> bool:
    """Send via Shortcuts Bridge TextDavid. Falls back to Twilio if bridge is down."""

def send_email(subject: str, html_body: str) -> bool:
    """Send via Gmail SMTP."""

def save_draft(filename: str, content: str) -> Path:
    """Save to data/drafts/. Returns the file path."""

def send_notification(title: str, body: str) -> bool:
    """Send via Shortcuts Bridge ShowNotification."""

def create_reminder(title: str, due_date: str | None = None) -> bool:
    """Create via Shortcuts Bridge CreateReminder."""
```

**Shortcuts Bridge client** (HTTP client for Shortcuts Bridge):
- POST to `http://localhost:9876/run` with `{"shortcut": "...", "input": "..."}`
- Include `X-Bridge-Token` header from env var
- 30-second timeout
- Return success/failure

## Deduplication: `data/history.json`

```json
{
  "literature": {
    "seen_ids": ["PMID:12345", "arxiv:2401.01234", "doi:10.1101/..."],
    "last_run": "2026-02-21T06:30:00"
  },
  "email_triage": {
    "seen_message_ids": ["msg-abc123"],
    "last_run": "2026-02-21T10:00:00"
  },
  "grants": {
    "seen_foas": ["RFA-LM-25-001", "PA-25-123"],
    "last_run": "2026-02-17T07:00:00"
  }
}
```

## Dependencies (`pyproject.toml`)

```toml
[project]
name = "local-ai-agents"
version = "0.1.0"
description = "Scheduled AI agents for literature, email, news, and grant monitoring"
requires-python = ">=3.11"
dependencies = [
    "openai",             # Ollama OpenAI-compat client (CORE dependency, not optional)
    "pyyaml",
    "python-dotenv",
    "httpx",
    "feedparser",         # RSS/Atom parsing
    "apscheduler>=3.10,<4",  # Scheduling (pin <4, API changed completely in v4)
]

[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-mock",
]
sms = [
    "twilio",             # Fallback SMS
]
```

**Changes from original spec:**
- `openai` moved from optional `[api]` to main deps (every agent needs it)
- `apscheduler` pinned `<4` to avoid breaking API changes
- `pytest-mock` added to dev deps

## Environment Variables (`.env`)

```bash
# Ollama (usually no auth needed, but configurable)
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=gemma3

# Shortcuts Bridge
SHORTCUTS_BRIDGE_TOKEN=<from shortcuts_bridge.local.json>

# Gmail SMTP
EMAIL_SENDER=your@gmail.com
EMAIL_RECIPIENT=your@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx

# Twilio fallback (optional)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
TWILIO_TO_NUMBER=
```

## Testing Strategy

1. **Unit tests:** Mock Ollama responses, test each source parser, test delivery formatting
2. **Integration test:** `python scripts/run_agent.py literature --dry-run` (fetches real data, calls Ollama, prints output instead of sending)
3. **Smoke test:** `python scripts/run_agent.py all --dry-run` before enabling the LaunchAgent

## Build Order (for Claude Code)

1. **Scaffold:** `pyproject.toml`, directory structure, `.env.example`, config YAML files
2. **LLM client:** `src/llm.py` -- Ollama via OpenAI compat, with health check
3. **Delivery layer:** `src/delivery.py` + `src/bridge_client.py` -- text, email, file, notification
4. **Sources:** `src/sources/` -- PubMed, arXiv, RSS, Gmail reader, NIH RePORTER
5. **Base agent:** `src/agents/base.py` -- fetch/summarize/deliver ABC
6. **Individual agents:** literature, email_triage, news, grants
7. **CLI runner:** `scripts/run_agent.py` with `--dry-run` flag
8. **Scheduler:** `scripts/scheduler.py` with APScheduler
9. **LaunchAgent plist:** `com.local-ai-agents.scheduler.plist`
10. **Tests:** Unit + integration tests
11. **CLAUDE.md:** Lean project doc following global guidelines

## Key Constraints

- All LLM calls go through **local Ollama only** -- no cloud API keys for inference
- Gemma 3 context window is 8K-128K depending on variant; keep prompts + content under 6K tokens to be safe
- Truncate email bodies, article abstracts, and feed content before sending to LLM
- Each agent run should complete in under 5 minutes (timeout the LLM call at 120s)
- Dedup everything -- never send the same paper/grant/email summary twice
- Graceful degradation: if Ollama is down, log the error and skip (don't crash the scheduler)
- If Shortcuts Bridge is down, fall back to email-only delivery
