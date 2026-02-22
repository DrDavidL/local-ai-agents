#!/usr/bin/env python3
"""Telegram bot: chat with your local Ollama from your phone.

Also supports triggering agents via /run commands or natural language.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
import httpx
import yaml

from src import llm
from src.agents.base import load_config, CONFIG_DIR
from src.agents.literature import LiteratureAgent
from src.agents.email_triage import EmailTriageAgent
from src.agents.news import NewsAgent
from src.agents.grants import GrantsAgent
from src.agents.current_events import CurrentEventsAgent

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Suppress httpx request logging — it includes the bot token in URLs
logging.getLogger("httpx").setLevel(logging.WARNING)

TELEGRAM_API = "https://api.telegram.org/bot{token}"
MAX_MSG_LEN = 4096  # Telegram's per-message character limit

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful personal assistant running locally via Ollama. "
    "Be concise and direct. Use markdown formatting when it helps readability."
)

# Per-chat conversation history: chat_id -> list of {"role": ..., "content": ...}
conversations: dict[int, list[dict[str, str]]] = defaultdict(list)


def load_telegram_config() -> dict:
    """Load config/telegram.yaml (personal system prompt). Returns {} if missing."""
    path = CONFIG_DIR / "telegram.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── Agent integration ─────────────────────────────────────────────────

AGENT_CLASSES = {
    "literature": LiteratureAgent,
    "email": EmailTriageAgent,
    "news": NewsAgent,
    "grants": GrantsAgent,
    "current": CurrentEventsAgent,
}

AGENT_LABELS = {
    "literature": "Literature",
    "email": "Email Triage",
    "news": "News",
    "grants": "Grants",
    "current": "Current Events",
}

# (keywords, agent_key) — checked in order; multi-word phrases first
AGENT_TRIGGERS = [
    (["current events", "middle east", "israel", "chicago"], "current"),
    (["paper", "papers", "literature", "pubmed", "arxiv", "research"], "literature"),
    (["email", "emails", "inbox", "mail", "gmail"], "email"),
    (["grant", "grants", "funding", "nih", "nsf"], "grants"),
    (["market", "markets", "stock", "stocks", "finance"], "current"),
    (["news", "headlines"], "news"),
    (["briefing"], "current"),
]

INTENT_SIGNALS = [
    "check", "run", "any", "what", "show", "get", "how",
    "new", "update", "latest", "recent", "?",
]


def detect_agent_intent(text: str) -> str | None:
    """Detect if the user wants to trigger an agent from natural language."""
    text_lower = text.lower()

    # Require an intent signal to avoid false positives
    if not any(signal in text_lower for signal in INTENT_SIGNALS):
        return None

    for keywords, agent_key in AGENT_TRIGGERS:
        for kw in keywords:
            if kw in text_lower:
                return agent_key
    return None


def run_agent_for_chat(agent_key: str, config: dict) -> dict | None:
    """Run an agent in dry-run mode and return its result."""
    agent_class = AGENT_CLASSES.get(agent_key)
    if not agent_class:
        return None
    agent = agent_class(config=config, dry_run=True)
    return agent.run()


def format_result(agent_key: str, result: dict | None) -> str:
    """Format an agent result into a Telegram-friendly message."""
    label = AGENT_LABELS.get(agent_key, agent_key)

    if result is None:
        return f"*{label}*: No new items found."

    lines: list[str] = []

    if agent_key == "literature":
        papers = result.get("papers", [])
        lines.append(f"*{label}* — {len(papers)} papers\n")
        lines.append(result.get("summary", ""))
        for p in papers:
            rel = p.get("clinical_relevance", "")
            rel_tag = f" [{rel}]" if rel else ""
            lines.append(f"\n{p['title']}{rel_tag}")
            if p.get("one_liner"):
                lines.append(f"  {p['one_liner']}")
            lines.append(f"  {p['url']}")

    elif agent_key == "news":
        items = result.get("items", [])
        lines.append(f"*{label}* — {len(items)} items\n")
        lines.append(result.get("headline_summary", ""))
        for item in items:
            cat = f" [{item.get('category', '')}]" if item.get("category") else ""
            lines.append(f"\n{item['title']}{cat}")
            if item.get("one_liner"):
                lines.append(f"  {item['one_liner']}")
            lines.append(f"  {item['url']}")

    elif agent_key == "email":
        items = result.get("items", [])
        urgent = result.get("urgent_count", 0)
        lines.append(f"*{label}* — {len(items)} emails, {urgent} urgent\n")
        lines.append(result.get("summary", ""))
        for item in items:
            priority = item.get("priority", "")
            icon = {"urgent": "!!", "action-needed": "!", "fyi": ""}.get(priority, "")
            lines.append(f"\n{icon} {item.get('subject', '')} (from {item.get('from', '')})")
            if item.get("one_liner"):
                lines.append(f"  {item['one_liner']}")
            if item.get("next_action"):
                lines.append(f"  Action: {item['next_action']}")

    elif agent_key == "grants":
        opps = result.get("opportunities", [])
        lines.append(f"*{label}* — {len(opps)} opportunities\n")
        lines.append(result.get("summary", ""))
        for opp in opps:
            rel = f" [{opp.get('relevance', '')}]" if opp.get("relevance") else ""
            lines.append(f"\n{opp['title']}{rel}")
            if opp.get("one_liner"):
                lines.append(f"  {opp['one_liner']}")
            if opp.get("deadline"):
                lines.append(f"  Deadline: {opp['deadline']}")
            lines.append(f"  {opp['url']}")

    elif agent_key == "current":
        items = result.get("items", [])
        lines.append(f"*{label}* — {len(items)} items\n")
        lines.append(result.get("briefing", ""))
        # Group by topic
        by_topic: dict[str, list] = {}
        for item in items:
            topic = item.get("topic", "other")
            by_topic.setdefault(topic, []).append(item)
        for topic, topic_items in by_topic.items():
            lines.append(f"\n_{topic}_")
            for item in topic_items:
                imp = item.get("importance", "")
                tag = f" [{imp}]" if imp else ""
                lines.append(f"  {item['title']}{tag}")
                if item.get("one_liner"):
                    lines.append(f"    {item['one_liner']}")
                lines.append(f"    {item['url']}")

    else:
        lines.append(f"*{label}* completed.")

    return "\n".join(lines)


# ── Ad-hoc search ────────────────────────────────────────────────────

# Patterns: "Research - fisetin", "papers on longevity", "News - Russia", etc.
SEARCH_PATTERNS = [
    # Dash/colon syntax: "Research - fisetin", "News: Russia"
    (re.compile(r"^(?:research|papers?|literature|pubmed|arxiv)\s*[-:]\s*(.+)", re.I), "research"),
    (re.compile(r"^(?:news|headlines)\s*[-:]\s*(.+)", re.I), "news"),
    # Preposition syntax: "papers on fisetin", "research about longevity"
    (re.compile(r"^(?:research|papers?|literature)\s+(?:on|about|for|regarding)\s+(.+)", re.I), "research"),
    (re.compile(r"^(?:news|headlines)\s+(?:about|on|from|in|regarding)\s+(.+)", re.I), "news"),
    # "look up fisetin"
    (re.compile(r"^look\s*up\s+(.+)", re.I), "research"),
]

# Medication lookup patterns
MED_PATTERNS = [
    # "med metformin", "medication lisinopril"
    (re.compile(r"^(?:med|meds|medication|drug|rx)\s*[-:]\s*(.+)", re.I), None),
    (re.compile(r"^(?:med|meds|medication|drug|rx)\s+(.+)", re.I), None),
    # "metformin interactions", "lisinopril side effects", "ibuprofen dosage"
    (re.compile(r"^(\w[\w\s-]*?)\s+(interactions?|side effects?|dosage|dosing|warnings?|contraindications?|overdose)$", re.I), "focus"),
    # "what is metformin", "what is lisinopril used for"
    (re.compile(r"^what\s+is\s+(\w[\w\s-]+?)(?:\s+used\s+for)?[?\s]*$", re.I), None),
    # "side effects of metformin", "interactions for lisinopril"
    (re.compile(r"^(?:side effects?|interactions?|dosage|warnings?|uses?)\s+(?:of|for)\s+(.+)", re.I), "focus_prefix"),
]

# Map focus keywords to relevant openFDA sections
FOCUS_SECTIONS = {
    "interaction": ["drug_interactions"],
    "interactions": ["drug_interactions"],
    "side effect": ["adverse_reactions"],
    "side effects": ["adverse_reactions"],
    "dosage": ["dosage_and_administration"],
    "dosing": ["dosage_and_administration"],
    "warning": ["warnings_and_cautions", "boxed_warning", "warnings"],
    "warnings": ["warnings_and_cautions", "boxed_warning", "warnings"],
    "contraindication": ["contraindications"],
    "contraindications": ["contraindications"],
    "overdose": ["overdosage"],
    "use": ["indications_and_usage"],
    "uses": ["indications_and_usage"],
}


def detect_search_query(text: str) -> tuple[str, str] | None:
    """Detect ad-hoc search intent. Returns (search_type, query) or None."""
    for pattern, search_type in SEARCH_PATTERNS:
        m = pattern.match(text.strip())
        if m:
            query = m.group(1).strip().rstrip("?.")
            if len(query) >= 2:
                return (search_type, query)
    return None


def ad_hoc_research(query: str, ollama_cfg: dict) -> str:
    """Search PubMed + arXiv for a query, LLM-rank results, format for Telegram."""
    from src.sources import pubmed, arxiv

    pm_articles = pubmed.search_and_fetch(query, max_results=10)
    arxiv_papers = arxiv.search(query, max_results=10)

    if not pm_articles and not arxiv_papers:
        return f"No research results found for: _{query}_"

    # Build numbered content for LLM
    numbered = []
    for i, art in enumerate(pm_articles, 1):
        numbered.append(
            f"{i}. [PubMed] {art.title}\n"
            f"   Authors: {art.authors}\n"
            f"   Abstract: {art.abstract[:500]}"
        )
    offset = len(pm_articles)
    for i, paper in enumerate(arxiv_papers, offset + 1):
        numbered.append(
            f"{i}. [arXiv] {paper.title}\n"
            f"   Authors: {paper.authors}\n"
            f"   Abstract: {paper.abstract[:500]}"
        )
    content = f"Query: {query}\n\n" + "\n\n".join(numbered)

    system_prompt = (
        "You are a research assistant. Given search results from PubMed and arXiv, "
        "rank them by relevance to the query. Return JSON with:\n"
        '{"summary": "1-2 sentence overview of findings", '
        '"top_results": [{"item_number": N, "title": "...", '
        '"one_liner": "why this is relevant", "relevance": "high/medium/low"}]}\n'
        "Return at most 5 top results, most relevant first."
    )

    base_url = ollama_cfg.get("base_url", "http://localhost:11434/v1")
    result = llm.structured_output(
        system_prompt, content,
        model=ollama_cfg.get("model", "gemma3"),
        base_url=base_url,
    )

    # Format — merge LLM ranking with source data for URLs (never send URLs to LLM)
    lines = [f"*Research: {query}*\n"]
    all_sources = list(pm_articles) + list(arxiv_papers)

    if result and result.get("summary"):
        lines.append(result["summary"])

    if result and result.get("top_results"):
        for item in result["top_results"]:
            idx = item.get("item_number", 0) - 1
            if 0 <= idx < len(all_sources):
                src = all_sources[idx]
                rel = item.get("relevance", "")
                tag = f" [{rel}]" if rel else ""
                lines.append(f"\n{item.get('title', src.title)}{tag}")
                if item.get("one_liner"):
                    lines.append(f"  {item['one_liner']}")
                lines.append(f"  {src.url}")
    else:
        for src in all_sources[:5]:
            lines.append(f"\n{src.title}")
            lines.append(f"  {src.url}")

    return "\n".join(lines)


def ad_hoc_news(query: str, ollama_cfg: dict) -> str:
    """Search Google News RSS for a query, LLM-summarize, format for Telegram."""
    from src.sources.rss import fetch_feed

    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    items = fetch_feed(url, source_name="Google News", max_items=15)

    if not items:
        return f"No news results found for: _{query}_"

    # Build numbered content for LLM
    numbered = []
    for i, item in enumerate(items, 1):
        numbered.append(f"{i}. {item.title} ({item.source})")
    content = f"Query: {query}\n\n" + "\n".join(numbered)

    system_prompt = (
        "You are a news analyst. Given news search results, summarize the key themes "
        "and rank by importance. Return JSON with:\n"
        '{"summary": "2-3 sentence briefing on the topic", '
        '"top_items": [{"item_number": N, "one_liner": "why this is significant"}]}\n'
        "Return at most 7 top items, most important first."
    )

    base_url = ollama_cfg.get("base_url", "http://localhost:11434/v1")
    result = llm.structured_output(
        system_prompt, content,
        model=ollama_cfg.get("model", "gemma3"),
        base_url=base_url,
    )

    # Format — merge LLM ranking with source data for URLs
    lines = [f"*News: {query}*\n"]

    if result and result.get("summary"):
        lines.append(result["summary"])

    if result and result.get("top_items"):
        for ranked in result["top_items"]:
            idx = ranked.get("item_number", 0) - 1
            if 0 <= idx < len(items):
                item = items[idx]
                lines.append(f"\n{item.title}")
                if ranked.get("one_liner"):
                    lines.append(f"  {ranked['one_liner']}")
                lines.append(f"  {item.url}")
    else:
        for item in items[:7]:
            lines.append(f"\n{item.title}")
            lines.append(f"  {item.url}")

    return "\n".join(lines)


def detect_med_query(text: str) -> tuple[str, str | None] | None:
    """Detect medication lookup intent. Returns (drug_name, focus_area) or None.

    focus_area is None for general lookups, or a keyword like 'interactions'.
    """
    for pattern, mode in MED_PATTERNS:
        m = pattern.match(text.strip())
        if not m:
            continue
        if mode == "focus":
            # Pattern: "metformin interactions" → groups: (drug, focus)
            drug = m.group(1).strip()
            focus = m.group(2).strip().lower()
            if len(drug) >= 3:
                return (drug, focus)
        elif mode == "focus_prefix":
            # Pattern: "side effects of metformin" → group 1 is the drug
            drug = m.group(1).strip()
            # Extract focus from the matched text prefix
            prefix = text.strip().split()[0].lower()
            focus_map = {"side": "side effects", "interaction": "interactions",
                         "interactions": "interactions", "dosage": "dosage",
                         "warning": "warnings", "warnings": "warnings",
                         "use": "uses", "uses": "uses"}
            focus = focus_map.get(prefix, None)
            if len(drug) >= 3:
                return (drug, focus)
        else:
            drug = m.group(1).strip().rstrip("?.!")
            if len(drug) >= 3:
                return (drug, None)
    return None


FDA_DISCLAIMER = "_Source: FDA drug labeling via openFDA. Not a substitute for professional medical advice._"

# Section display order and character budgets for general queries.
# (api_key, display_label, max_chars)
RX_DISPLAY = [
    ("indications_and_usage", "Indications", 400),
    ("dosage_and_administration", "Dosing", 1000),
    ("boxed_warning", "Boxed Warning", 400),
    ("contraindications", "Contraindications", 300),
    ("adverse_reactions", "Common Side Effects", 350),
    ("drug_interactions", "Drug Interactions", 500),
]

OTC_DISPLAY = [
    ("active_ingredient", "Active Ingredient", 200),
    ("purpose", "Purpose", 100),
    ("indications_and_usage", "Uses", 300),
    ("dosage_and_administration", "Directions", 500),
    ("warnings", "Warnings", 500),
    ("do_not_use", "Do Not Use", 250),
    ("stop_use", "Stop Use", 250),
]


# FDA section heading pattern — explicit names to avoid consuming drug names
_FDA_HEADING_RE = re.compile(
    r"^(?:\d+\s+)?(?:"
    r"INDICATIONS\s+(?:AND|&)\s+USAGE"
    r"|DOSAGE\s+(?:AND|&)\s+ADMINISTRATION"
    r"|DOSAGE\s+FORMS\s+(?:AND|&)\s+STRENGTHS"
    r"|CONTRAINDICATIONS?"
    r"|WARNINGS?\s+AND\s+(?:PRECAUTIONS|CAUTIONS)"
    r"|WARNINGS?"
    r"|ADVERSE\s+REACTIONS?"
    r"|DRUG\s+INTERACTIONS?"
    r"|USE\s+IN\s+SPECIFIC\s+POPULATIONS?"
    r"|OVERDOSAGE"
    r"|CLINICAL\s+PHARMACOLOGY"
    r"|MECHANISM\s+OF\s+ACTION"
    r"|DESCRIPTION"
    r")\s+",
    re.I,
)


def _clean_fda_text(text: str) -> str:
    """Clean FDA label text for Telegram display.

    Strips section headings, internal cross-references, and normalizes whitespace.
    The clinical content is preserved verbatim.
    """
    # Strip leading section number + known FDA heading (won't consume drug names)
    text = _FDA_HEADING_RE.sub("", text)
    # Strip "BOXED WARNING WARNING:" prefix
    text = re.sub(r"^BOXED WARNING\s+(?:WARNING:\s*)?", "", text, flags=re.I)
    # Strip OTC-style heading words that duplicate our display label
    text = re.sub(r"^(?:Uses|Directions|Purpose|Warnings|Active ingredient[^)]*\))\s+", "", text, flags=re.I)
    # Strip cross-reference brackets: [see ...] and [ see ... ]
    text = re.sub(r"\s*\[\s*see [^\]]+\]", "", text)
    # Strip inline section refs: ( 2.1 ) or ( 4 , 5.1 ) or (5.1)
    text = re.sub(r"\s*\(\s*\d[\d.,\s]*\)", "", text)
    # Strip subsection numbers like "2.1 Adult Dosage" (followed by uppercase = heading)
    # but NOT dose values like "2.5 mg" (followed by lowercase = units)
    text = re.sub(r"\s\d+\.\d+\s+(?=[A-Z])", " ", text)
    # Normalize whitespace
    text = re.sub(r"  +", " ", text)
    return text.strip()


_FDA_BREAK_RE = re.compile(
    r"(?<=\.)\s+(?="
    # Population / age subheadings
    r"(?:Adults|Children|Pediatric Patients?|Neonates?|Geriatric Patients?|Infants?)"
    r"|(?:For (?:the treatment|patients?|adults|children|pediatric|neonates?))"
    # Organ impairment subheadings
    r"|(?:(?:Renal|Hepatic|Hepatic and Renal)\s+Impairment)"
    # Dose-adjustment subheadings
    r"|(?:(?:Dosage|Dose)\s+(?:Adjustment|Modification|Reduction|for|in|and))"
    # Administration / preparation
    r"|(?:(?:Important|Administration|Preparation|Reconstitution|Directions))"
    # Duration / switching
    r"|(?:(?:Duration|Switching|Conversion|Maintenance|Maximum))"
    # Table references
    r"|(?:Table\s+\d+)"
    r")",
    re.I,
)


def _add_paragraph_breaks(text: str) -> str:
    """Insert paragraph breaks before FDA inline subheadings."""
    return _FDA_BREAK_RE.sub("\n\n", text)


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text at a sentence boundary, or at max_chars with ellipsis."""
    if len(text) <= max_chars:
        return text
    # Try to cut at last sentence boundary before max_chars
    truncated = text[:max_chars]
    last_period = truncated.rfind(". ")
    if last_period > max_chars // 2:
        return truncated[: last_period + 1]
    return truncated.rstrip() + "..."


def ad_hoc_medication(drug_name: str, focus: str | None) -> str:
    """Look up a drug via openFDA and display FDA label text directly (no LLM)."""
    from src.sources.openfda import search_drug_options

    options = search_drug_options(drug_name)

    if not options:
        return f"No FDA drug label found for: _{drug_name}_"

    # Show the first result (single-ingredient, sorted to top)
    label = options[0]
    result = _format_med_label(label, drug_name, focus)

    # Mention other products if available
    if len(options) > 1:
        others = options[1:]
        names = [o.brand_name or o.generic_name for o in others]
        result += "\n\n_Also available:_ " + ", ".join(names)
        result += f"\n_Use_ `/med <name>` _to look up a specific product._"

    return result


def _format_med_label(label, drug_name: str, focus: str | None) -> str:
    """Format a single drug label for display."""
    display_name = label.brand_name or label.generic_name or drug_name
    if label.brand_name and label.generic_name:
        display_name = f"{label.brand_name} ({label.generic_name.lower()})"

    sections = label.sections
    if not sections:
        return f"FDA label found for _{display_name}_ but no content sections available."

    is_otc = "purpose" in sections or "do_not_use" in sections

    if focus:
        return _format_med_focused(display_name, focus, sections)
    elif is_otc:
        return _format_med_display(display_name, sections, OTC_DISPLAY)
    else:
        return _format_med_display(display_name, sections, RX_DISPLAY)



def _format_med_display(
    display_name: str,
    sections: dict[str, str],
    display_plan: list[tuple[str, str, int]],
) -> str:
    """Format a general medication lookup using hardcoded layout and FDA text."""
    lines = [f"*{display_name}*"]

    for api_key, heading, budget in display_plan:
        raw = sections.get(api_key, "")
        if not raw:
            continue
        cleaned = _add_paragraph_breaks(_clean_fda_text(raw))
        if not cleaned:
            continue
        text = _truncate(cleaned, budget)
        lines.append(f"\n*{heading}*\n{text}")

    lines.append(f"\n{FDA_DISCLAIMER}")
    return "\n".join(lines)


def _format_med_focused(
    display_name: str,
    focus: str,
    sections: dict[str, str],
) -> str:
    """Format a focused medication lookup (e.g., just interactions)."""
    relevant_keys = FOCUS_SECTIONS.get(focus, [])
    heading = focus.replace("_", " ").title()

    # Collect text from all matching sections
    parts = []
    for key in relevant_keys:
        raw = sections.get(key, "")
        if raw:
            parts.append(_add_paragraph_breaks(_clean_fda_text(raw)))

    if not parts:
        return f"No _{heading.lower()}_ section found in the label for _{display_name}_."

    combined = "\n\n".join(parts)
    # Focused queries get a generous budget (most of Telegram's 4096 limit)
    text = _truncate(combined, 3500)

    lines = [f"*{display_name} — {heading}*\n", text, f"\n{FDA_DISCLAIMER}"]
    return "\n".join(lines)


# ── Telegram API helpers ──────────────────────────────────────────────

HELP_TEXT = """*Available commands:*
/help  — Show this message
/clear — Clear conversation history
/run <agent> — Run an agent (literature, email, news, grants, current, all)
/search research <query> — Search PubMed + arXiv
/search news <query> — Search Google News
/med <drug> — Look up a medication (FDA label)
/med <drug> interactions — Focus on drug interactions
/agents — List available agents
/id    — Show your Telegram chat ID
/model — Show current LLM model
/system <prompt> — Change the system prompt

*Or just ask naturally:*
"Any new papers?" "Check my email" "What's in the news?"
"Research fisetin" "News - Russia" "Papers on longevity"
"What is metformin?" "Lisinopril side effects\""""


def tg_request(token: str, method: str, **kwargs) -> dict:
    """Make a Telegram Bot API request."""
    url = f"{TELEGRAM_API.format(token=token)}/{method}"
    resp = httpx.post(url, json=kwargs, timeout=30)
    return resp.json()


def get_updates(token: str, offset: int | None = None, timeout: int = 30) -> list[dict]:
    """Long-poll for new messages."""
    params: dict = {"timeout": timeout, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    url = f"{TELEGRAM_API.format(token=token)}/getUpdates"
    resp = httpx.get(url, params=params, timeout=timeout + 10)
    return resp.json().get("result", [])


def send_message(token: str, chat_id: int, text: str) -> None:
    """Send a message, chunking if needed. Tries Markdown, falls back to plain."""
    for i in range(0, len(text), MAX_MSG_LEN):
        chunk = text[i : i + MAX_MSG_LEN]
        resp = tg_request(token, "sendMessage",
                          chat_id=chat_id, text=chunk, parse_mode="Markdown")
        if not resp.get("ok"):
            # Markdown parse failed — retry as plain text
            tg_request(token, "sendMessage", chat_id=chat_id, text=chunk)


def send_typing(token: str, chat_id: int) -> None:
    """Show 'typing...' indicator."""
    tg_request(token, "sendChatAction", chat_id=chat_id, action="typing")


# ── Message handling ──────────────────────────────────────────────────


def handle_agent_run(
    token: str,
    chat_id: int,
    agent_key: str,
    config: dict,
) -> None:
    """Run an agent and send the result via Telegram."""
    label = AGENT_LABELS.get(agent_key, agent_key)
    send_message(token, chat_id, f"Running {label} agent...")
    send_typing(token, chat_id)

    try:
        result = run_agent_for_chat(agent_key, config)
        reply = format_result(agent_key, result)
    except Exception as exc:
        logger.error("Agent '%s' failed: %s", agent_key, exc)
        reply = f"Agent {label} failed: {exc}"

    send_message(token, chat_id, reply)


def handle_message(
    token: str,
    chat_id: int,
    text: str,
    config: dict,
    ollama_cfg: dict,
    tg_cfg: dict,
    allowed_ids: set[int],
    base_system_prompt: str,
    custom_prompts: dict[int, str],
) -> None:
    """Process an incoming message."""
    max_history = tg_cfg.get("max_history", 20)

    # Auth check (fail-closed: /id always works for setup, everything else requires allowlist)
    if chat_id not in allowed_ids:
        if text.strip().startswith("/id"):
            send_message(token, chat_id, f"Your chat ID: `{chat_id}`")
            return
        logger.warning("Unauthorized chat_id=%d", chat_id)
        send_message(token, chat_id,
                     f"Unauthorized. Your chat ID is `{chat_id}`. "
                     "Add it to TELEGRAM\\_ALLOWED\\_CHAT\\_IDS in .env.")
        return

    # ── Slash commands ────────────────────────────────────────────────
    if text.startswith("/"):
        cmd_parts = text.split()
        cmd = cmd_parts[0].lower().split("@")[0]  # strip @botname suffix

        if cmd == "/start":
            send_message(token, chat_id,
                         "Hello! I'm your local AI assistant powered by Ollama.\n\n"
                         f"Model: `{ollama_cfg.get('model', 'unknown')}`\n\n"
                         "Send me anything, or /help for commands.")
            return

        if cmd == "/help":
            send_message(token, chat_id, HELP_TEXT)
            return

        if cmd == "/clear":
            conversations[chat_id] = []
            send_message(token, chat_id, "Conversation cleared.")
            return

        if cmd == "/id":
            send_message(token, chat_id, f"Your chat ID: `{chat_id}`")
            return

        if cmd == "/model":
            send_message(token, chat_id, f"Model: `{ollama_cfg.get('model', 'unknown')}`")
            return

        if cmd == "/agents":
            agent_list = "\n".join(
                f"  `{key}` — {label}" for key, label in AGENT_LABELS.items()
            )
            send_message(token, chat_id,
                         f"*Available agents:*\n{agent_list}\n\n"
                         "Use `/run <agent>` or `/run all`")
            return

        if cmd == "/run":
            arg = cmd_parts[1].lower() if len(cmd_parts) > 1 else ""
            if arg == "all":
                for key in AGENT_CLASSES:
                    handle_agent_run(token, chat_id, key, config)
                return
            if arg in AGENT_CLASSES:
                handle_agent_run(token, chat_id, arg, config)
                return
            agent_names = ", ".join(AGENT_CLASSES.keys())
            send_message(token, chat_id,
                         f"Usage: `/run <agent>`\nAgents: {agent_names}, all")
            return

        if cmd == "/search":
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                send_message(token, chat_id,
                             "Usage: `/search research <query>` or `/search news <query>`")
                return
            search_type = parts[1].lower()
            query = parts[2].strip()
            if search_type in ("research", "papers", "literature", "pubmed", "arxiv"):
                send_message(token, chat_id, f"Searching research for _{query}_...")
                send_typing(token, chat_id)
                reply = ad_hoc_research(query, ollama_cfg)
                send_message(token, chat_id, reply)
            elif search_type in ("news", "headlines"):
                send_message(token, chat_id, f"Searching news for _{query}_...")
                send_typing(token, chat_id)
                reply = ad_hoc_news(query, ollama_cfg)
                send_message(token, chat_id, reply)
            else:
                send_message(token, chat_id,
                             "Usage: `/search research <query>` or `/search news <query>`")
            return

        if cmd == "/med":
            rest = text[len("/med"):].strip()
            if not rest:
                send_message(token, chat_id,
                             "Usage: `/med <drug>` or `/med <drug> interactions`")
                return
            # Check for focus keyword at the end
            focus = None
            for kw in FOCUS_SECTIONS:
                if rest.lower().endswith(f" {kw}"):
                    focus = kw
                    rest = rest[:-(len(kw) + 1)].strip()
                    break
            send_message(token, chat_id, f"Looking up _{rest}_...")
            send_typing(token, chat_id)
            reply = ad_hoc_medication(rest, focus)
            send_message(token, chat_id, reply)
            return

        if cmd == "/system":
            new_prompt = text[len("/system"):].strip()
            if new_prompt:
                custom_prompts[chat_id] = new_prompt
                conversations[chat_id] = []  # reset history with new persona
                send_message(token, chat_id, "System prompt updated. History cleared.")
            else:
                current = custom_prompts.get(chat_id, base_system_prompt)
                send_message(token, chat_id, f"Current system prompt:\n\n{current}")
            return

    # ── Natural language medication detection ─────────────────────────
    med = detect_med_query(text)
    if med:
        drug_name, focus = med
        send_message(token, chat_id, f"Looking up _{drug_name}_...")
        send_typing(token, chat_id)
        reply = ad_hoc_medication(drug_name, focus)
        send_message(token, chat_id, reply)
        return

    # ── Natural language search detection ─────────────────────────────
    search = detect_search_query(text)
    if search:
        search_type, query = search
        if search_type == "research":
            send_message(token, chat_id, f"Searching research for _{query}_...")
            send_typing(token, chat_id)
            reply = ad_hoc_research(query, ollama_cfg)
        else:
            send_message(token, chat_id, f"Searching news for _{query}_...")
            send_typing(token, chat_id)
            reply = ad_hoc_news(query, ollama_cfg)
        send_message(token, chat_id, reply)
        return

    # ── Natural language agent detection ──────────────────────────────
    agent_key = detect_agent_intent(text)
    if agent_key:
        handle_agent_run(token, chat_id, agent_key, config)
        return

    # ── Regular chat with LLM ─────────────────────────────────────────
    send_typing(token, chat_id)

    conversations[chat_id].append({"role": "user", "content": text})

    # Trim history
    if len(conversations[chat_id]) > max_history * 2:
        conversations[chat_id] = conversations[chat_id][-(max_history * 2):]

    # Check Ollama health
    base_url = ollama_cfg.get("base_url", "http://localhost:11434/v1")
    ollama_root = base_url.replace("/v1", "")
    if not llm.health_check(ollama_root):
        send_message(token, chat_id, "Ollama is not running. Start it and try again.")
        conversations[chat_id].pop()
        return

    sys_prompt = custom_prompts.get(chat_id, base_system_prompt)
    temperature = tg_cfg.get("temperature", ollama_cfg.get("temperature", 0.7))

    response = llm.chat(
        conversations[chat_id],
        system_prompt=sys_prompt,
        model=ollama_cfg.get("model", "gemma3"),
        max_tokens=ollama_cfg.get("max_tokens", 2048),
        temperature=temperature,
        base_url=base_url,
    )

    if response:
        conversations[chat_id].append({"role": "assistant", "content": response})
        send_message(token, chat_id, response)
    else:
        send_message(token, chat_id, "Sorry, I couldn't generate a response.")
        conversations[chat_id].pop()


# ── Main loop ─────────────────────────────────────────────────────────


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    # Load settings from agents.yaml and config/telegram.yaml
    config = load_config()
    ollama_cfg = config.get("ollama", {})
    tg_cfg = config.get("telegram", {})
    tg_personal = load_telegram_config()

    # System prompt: personal config > default
    base_system_prompt = tg_personal.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
    logger.info("System prompt loaded (%d chars)", len(base_system_prompt))

    # Parse allowed chat IDs (fail-closed: refuse all if not configured)
    allowed_str = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
    allowed_ids: set[int] = set()
    if allowed_str.strip():
        allowed_ids = {int(x.strip()) for x in allowed_str.split(",") if x.strip()}
        logger.info("Restricted to chat IDs: %s", allowed_ids)
    else:
        logger.error(
            "TELEGRAM_ALLOWED_CHAT_IDS not set. Bot will reject all messages. "
            "Send /id to the bot, then add your chat ID to .env."
        )

    # Per-chat custom system prompts (in-memory, overridden via /system command)
    custom_prompts: dict[int, str] = {}

    # Verify Telegram token
    me = tg_request(token, "getMe")
    if not me.get("ok"):
        logger.error("Invalid Telegram token")
        sys.exit(1)
    bot_name = me["result"]["username"]
    logger.info("Bot started: @%s (model: %s)", bot_name, ollama_cfg.get("model"))
    logger.info("Agents available: %s", ", ".join(AGENT_CLASSES.keys()))

    offset: int | None = None
    while True:
        try:
            updates = get_updates(token, offset)
            for update in updates:
                update_id = update.get("update_id")
                if update_id is None:
                    continue
                offset = update_id + 1
                msg = update.get("message")
                if not isinstance(msg, dict):
                    continue
                chat = msg.get("chat")
                if not isinstance(chat, dict):
                    continue
                chat_id = chat.get("id")
                text = msg.get("text", "")
                if chat_id and text:
                    logger.info("chat_id=%d: %s", chat_id, text[:80])
                    handle_message(token, chat_id, text, config, ollama_cfg,
                                   tg_cfg, allowed_ids, base_system_prompt,
                                   custom_prompts)
        except httpx.TimeoutException:
            continue  # normal for long polling
        except KeyboardInterrupt:
            logger.info("Bot shutting down")
            break
        except Exception as exc:
            logger.error("Error in update loop: %s", exc, exc_info=True)


if __name__ == "__main__":
    main()
