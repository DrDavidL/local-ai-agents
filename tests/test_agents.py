"""Tests for agents."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.agents.literature import LiteratureAgent
from src.agents.email_triage import EmailTriageAgent
from src.agents.news import NewsAgent
from src.agents.grants import GrantsAgent


MOCK_CONFIG = {
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "gemma3",
        "temperature": 0.3,
        "max_tokens": 2048,
    },
    "delivery": {
        "shortcuts_bridge": {"url": "http://localhost:9876"},
        "drafts_dir": "data/drafts",
    },
    "agents": {
        "literature": {"enabled": True, "delivery": [], "max_items": 5},
        "email_triage": {"enabled": True, "gmail_label": "AI-REVIEW", "delivery": [], "max_items": 20},
        "news": {"enabled": True, "delivery": [], "max_items": 8},
        "grants": {"enabled": True, "delivery": [], "max_items": 10},
    },
}


class TestLiteratureAgent:
    @patch("src.agents.literature.pubmed")
    @patch("src.agents.literature.arxiv")
    @patch("src.agents.literature.biorxiv")
    @patch("src.agents.literature.load_feeds_config")
    def test_fetch(self, mock_feeds, mock_bio, mock_arxiv, mock_pubmed):
        mock_feeds.return_value = {
            "literature": {
                "search_terms": ["test query"],
                "arxiv_categories": ["cs.AI"],
                "biorxiv_subjects": ["bioinformatics"],
            }
        }
        mock_pubmed.search_and_fetch.return_value = [
            MagicMock(pmid="123", title="Test", authors="A B", abstract="...",
                      source="J Test", url="http://test", pub_date="2026"),
        ]
        mock_arxiv.search.return_value = []
        mock_bio.fetch.return_value = []

        agent = LiteratureAgent(config=MOCK_CONFIG, dry_run=True)
        items = agent.fetch()
        assert len(items) == 1
        assert items[0]["id"] == "PMID:123"

    def test_dedup(self):
        agent = LiteratureAgent(config=MOCK_CONFIG, dry_run=True)
        items = [{"id": "PMID:1"}, {"id": "PMID:2"}, {"id": "PMID:3"}]
        seen = {"PMID:1", "PMID:3"}
        result = agent.dedup(items, seen)
        assert len(result) == 1
        assert result[0]["id"] == "PMID:2"


class TestEmailTriageAgent:
    @patch("src.agents.email_triage.gmail")
    def test_fetch(self, mock_gmail):
        mock_gmail.read_label.return_value = [
            MagicMock(
                message_id="msg1", from_addr="test@test.com",
                subject="Test", date="2026-01-01", body="Hello", attachments="",
            ),
        ]

        agent = EmailTriageAgent(config=MOCK_CONFIG, dry_run=True)
        items = agent.fetch()
        assert len(items) == 1
        assert items[0]["from"] == "test@test.com"


class TestNewsAgent:
    @patch("src.agents.news.rss")
    @patch("src.agents.news.load_feeds_config")
    def test_fetch(self, mock_feeds, mock_rss):
        mock_feeds.return_value = {
            "news": {
                "rss_feeds": [{"name": "Test Feed", "url": "http://test.com/feed"}],
            }
        }
        mock_rss.fetch_multiple.return_value = [
            MagicMock(title="AI News", url="http://test.com/1",
                      source="Test", summary="...", published="2026-01-01"),
        ]

        agent = NewsAgent(config=MOCK_CONFIG, dry_run=True)
        items = agent.fetch()
        assert len(items) == 1
        assert items[0]["title"] == "AI News"


class TestGrantsAgent:
    @patch("src.agents.grants.grants_gov")
    @patch("src.agents.grants.nih_reporter")
    @patch("src.agents.grants.load_grants_config")
    def test_fetch(self, mock_grants_config, mock_nih, mock_gov):
        mock_grants_config.return_value = {
            "search_terms": ["clinical informatics"],
            "nih": {"activity_codes": ["R01"], "ic_codes": ["NLM"]},
            "grants_gov": {"categories": ["Health"]},
        }
        mock_nih.search.return_value = [
            MagicMock(
                foa_number="RFA-LM-25-001", title="Test Grant",
                agency="NIH", ic_code="NLM", funding_range="$500,000",
                deadline="2026-06-01", url="http://test", abstract="...",
            ),
        ]
        mock_gov.search.return_value = []

        agent = GrantsAgent(config=MOCK_CONFIG, dry_run=True)
        items = agent.fetch()
        assert len(items) == 1
        assert items[0]["foa_number"] == "RFA-LM-25-001"
