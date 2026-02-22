#!/usr/bin/env python3
"""Telegram bot: chat with your local Ollama from your phone."""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
import httpx

import yaml

from src import llm
from src.agents.base import load_config, CONFIG_DIR

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

HELP_TEXT = """Available commands:
/help  — Show this message
/clear — Clear conversation history
/id    — Show your Telegram chat ID
/model — Show current LLM model
/system <prompt> — Change the system prompt for this session"""


# ── Telegram API helpers ──────────────────────────────────────────────


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


def handle_message(
    token: str,
    chat_id: int,
    text: str,
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

    # Commands
    if text.startswith("/"):
        cmd = text.split()[0].lower().split("@")[0]  # strip @botname suffix

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

    # Regular chat message
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
        conversations[chat_id].pop()  # remove the unanswered message
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
        conversations[chat_id].pop()  # remove the unanswered user message


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
        logger.error("Invalid Telegram token: %s", me)
        sys.exit(1)
    bot_name = me["result"]["username"]
    logger.info("Bot started: @%s (model: %s)", bot_name, ollama_cfg.get("model"))

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
                    handle_message(token, chat_id, text, ollama_cfg, tg_cfg,
                                   allowed_ids, base_system_prompt,
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
