"""Tests for LLM client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.llm import summarize, structured_output, health_check


class TestHealthCheck:
    @patch("src.llm.httpx.get")
    def test_healthy(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        assert health_check() is True

    @patch("src.llm.httpx.get")
    def test_unhealthy(self, mock_get):
        mock_get.side_effect = Exception("connection refused")
        assert health_check() is False


class TestSummarize:
    @patch("src.llm.get_client")
    def test_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Summary text"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = summarize("system prompt", "user content")
        assert result == "Summary text"

    @patch("src.llm.get_client")
    def test_returns_none_on_failure(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("boom")
        mock_get_client.return_value = mock_client

        result = summarize("system", "content", max_retries=0)
        assert result is None


class TestStructuredOutput:
    @patch("src.llm.get_client")
    def test_valid_json(self, mock_get_client):
        mock_client = MagicMock()
        expected = {"summary": "test", "items": []}
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps(expected)))
        ]
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = structured_output("system", "content")
        assert result == expected

    @patch("src.llm.get_client")
    def test_invalid_json_returns_none(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="not valid json"))
        ]
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = structured_output("system", "content")
        assert result is None
