"""Tests for data sources."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.sources import pubmed, arxiv, rss, grants_gov


class TestPubMed:
    @patch("src.sources.pubmed.httpx.get")
    def test_search(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "esearchresult": {"idlist": ["123", "456"]}
        }
        mock_get.return_value = mock_resp

        result = pubmed.search("test query")
        assert result == ["123", "456"]

    @patch("src.sources.pubmed.httpx.get")
    def test_search_failure(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        result = pubmed.search("test query")
        assert result == []

    def test_parse_xml_valid(self):
        xml = """<?xml version="1.0"?>
        <PubmedArticleSet>
            <PubmedArticle>
                <MedlineCitation>
                    <PMID>12345</PMID>
                    <Article>
                        <ArticleTitle>Test Paper</ArticleTitle>
                        <AuthorList>
                            <Author><LastName>Smith</LastName><Initials>J</Initials></Author>
                        </AuthorList>
                        <Abstract>
                            <AbstractText>This is an abstract.</AbstractText>
                        </Abstract>
                        <Journal>
                            <Title>Test Journal</Title>
                            <JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue>
                        </Journal>
                    </Article>
                </MedlineCitation>
            </PubmedArticle>
        </PubmedArticleSet>"""

        articles = pubmed._parse_xml(xml)
        assert len(articles) == 1
        assert articles[0].pmid == "12345"
        assert articles[0].title == "Test Paper"
        assert "Smith" in articles[0].authors


class TestArxiv:
    @patch("src.sources.arxiv.httpx.get")
    def test_search_failure(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        result = arxiv.search("test query")
        assert result == []


class TestRSS:
    @patch("src.sources.rss.feedparser.parse")
    def test_fetch_feed(self, mock_parse):
        mock_parse.return_value = MagicMock(
            bozo=False,
            feed={"title": "Test Feed"},
            entries=[
                {
                    "title": "Article 1",
                    "link": "http://test.com/1",
                    "summary": "Summary",
                    "published": "2026-01-01",
                },
            ],
        )
        items = rss.fetch_feed("http://test.com/feed")
        assert len(items) == 1
        assert items[0].title == "Article 1"

    @patch("src.sources.rss.feedparser.parse")
    def test_fetch_feed_error(self, mock_parse):
        mock_parse.return_value = MagicMock(
            bozo=True,
            bozo_exception=Exception("parse error"),
            entries=[],
        )
        items = rss.fetch_feed("http://bad.com/feed")
        assert items == []


class TestGrantsGov:
    @patch("src.sources.grants_gov.feedparser.parse")
    def test_search_with_keywords(self, mock_parse):
        mock_parse.return_value = MagicMock(
            bozo=False,
            entries=[
                {
                    "title": "AI in Healthcare Grant",
                    "summary": "Funding for clinical informatics research",
                    "id": "GRANT-001",
                    "link": "http://grants.gov/1",
                    "published": "2026-01-01",
                    "author": "HHS",
                },
                {
                    "title": "Agriculture Grant",
                    "summary": "Farming research",
                    "id": "GRANT-002",
                    "link": "http://grants.gov/2",
                    "published": "2026-01-01",
                    "author": "USDA",
                },
            ],
        )
        results = grants_gov.search(keywords=["clinical informatics"])
        assert len(results) == 1
        assert "Healthcare" in results[0].title
