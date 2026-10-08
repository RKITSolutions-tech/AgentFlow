"""Unit tests for web research integration (task 49.3-49.4)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.agents.web_research import LayeredFinding, LayeredResearchContext, WebResearchEngine


class TestLayeredFinding:
    """Tests for LayeredFinding dataclass."""

    def test_layered_finding_stores_metadata(self):
        """LayeredFinding stores text, layer, URL, and confidence."""
        finding = LayeredFinding(
            text="Important discovery",
            source_layer="web_fetch",
            url="https://example.com/article",
            content_excerpt="This is important...",
            confidence="verified",
        )
        assert finding.text == "Important discovery"
        assert finding.source_layer == "web_fetch"
        assert finding.url == "https://example.com/article"
        assert finding.confidence == "verified"


class TestLayeredResearchContext:
    """Tests for LayeredResearchContext."""

    def test_empty_context_renders_empty_prompt(self):
        """Empty context renders empty string."""
        context = LayeredResearchContext()
        prompt = context.to_prompt_text()
        assert prompt == ""

    def test_context_organizes_findings_by_layer(self):
        """Context separates findings by layer in prompt."""
        context = LayeredResearchContext()
        context.repository_findings.append(
            LayeredFinding("Repo finding", "repository")
        )
        context.knowledge_base_findings.append(
            LayeredFinding("KB entry", "knowledge_base")
        )
        context.web_findings.append(
            LayeredFinding("Web content", "web_fetch", url="https://example.com")
        )

        prompt = context.to_prompt_text()
        assert "Repository context:" in prompt
        assert "Repo finding" in prompt
        assert "Knowledge base entries:" in prompt
        assert "KB entry" in prompt
        assert "Web research findings:" in prompt
        assert "Web content" in prompt
        assert "https://example.com" in prompt

    def test_summary_counts(self):
        """summary_counts() returns layer breakdown."""
        context = LayeredResearchContext()
        context.repository_findings = [LayeredFinding("R1", "repository"), LayeredFinding("R2", "repository")]
        context.knowledge_base_findings = [LayeredFinding("K1", "knowledge_base")]
        context.web_findings = [
            LayeredFinding("W1", "web_search"),
            LayeredFinding("W2", "web_fetch"),
        ]

        counts = context.summary_counts()
        assert counts["repository"] == 2
        assert counts["knowledge_base"] == 1
        assert counts["web_search"] == 1
        assert counts["web_fetch"] == 1


class TestWebResearchEngine:
    """Tests for WebResearchEngine."""

    def test_engine_creates_with_default_fetcher(self):
        """Engine defaults to HostWebFetcher."""
        engine = WebResearchEngine()
        assert engine._fetcher is not None

    def test_engine_estimates_max_fetches_from_cost(self):
        """Max fetches estimated from cost limit (1000 per $1)."""
        engine = WebResearchEngine(cost_limit_usd=0.01)
        assert engine._max_fetches >= 1
        engine = WebResearchEngine(cost_limit_usd=1.0)
        assert engine._max_fetches >= 100

    def test_fetch_for_question_returns_empty_context_on_empty_question(self):
        """Empty question returns empty context."""
        engine = WebResearchEngine()
        context = engine.fetch_for_question("")
        assert not context.web_findings

    def test_fetch_for_question_builds_queries(self):
        """fetch_for_question builds queries from question."""
        mock_fetcher = MagicMock()
        mock_fetcher.search.return_value = []
        engine = WebResearchEngine(fetcher=mock_fetcher)

        context = engine.fetch_for_question("What is machine learning?", max_queries=2)

        # Verify search was called (queries built from question)
        assert mock_fetcher.search.called

    def test_search_and_collect_urls_deduplicates(self):
        """Deduplicates URLs across queries."""
        mock_fetcher = MagicMock()
        mock_fetcher.search.side_effect = [
            [{"url": "https://a.com", "title": "A"}],
            [{"url": "https://a.com", "title": "A"}],  # Duplicate
        ]
        engine = WebResearchEngine(fetcher=mock_fetcher)

        urls = engine._search_and_collect_urls(["query1", "query2"], max_per_query=5)

        assert len(urls) == 1
        assert urls[0] == "https://a.com"

    def test_fetch_and_add_finding_handles_fetch_errors(self):
        """Fetch errors are caught and skipped gracefully."""
        from app.agents.web_fetcher import WebFetcherOperationError

        mock_fetcher = MagicMock()
        mock_fetcher.cache_check.return_value = None
        mock_fetcher.fetch.side_effect = WebFetcherOperationError("Network error")
        engine = WebResearchEngine(fetcher=mock_fetcher)

        context = LayeredResearchContext()
        # Should not raise, should not add finding
        engine._fetch_and_add_finding("https://example.com", context)

        assert len(context.web_findings) == 0

    def test_fetch_and_add_finding_uses_cached_content(self):
        """Uses cached content when available."""
        mock_fetcher = MagicMock()
        mock_fetcher.cache_check.return_value = {
            "content": "Cached content here",
            "url": "https://example.com",
        }
        mock_fetcher.fetch.return_value = None  # Should not be called
        engine = WebResearchEngine(fetcher=mock_fetcher)

        context = LayeredResearchContext()
        engine._fetch_and_add_finding("https://example.com", context)

        assert len(context.web_findings) == 1
        assert "Cached" in context.web_findings[0].text
        mock_fetcher.fetch.assert_not_called()

    def test_fetch_and_add_finding_fetches_new_content(self):
        """Fetches and caches new content."""
        mock_fetcher = MagicMock()
        mock_fetcher.cache_check.return_value = None
        mock_fetcher.fetch.return_value = {
            "content": "Fresh content fetched",
            "url": "https://example.com",
        }
        engine = WebResearchEngine(fetcher=mock_fetcher)

        context = LayeredResearchContext()
        engine._fetch_and_add_finding("https://example.com", context)

        assert len(context.web_findings) == 1
        assert "Fresh" in context.web_findings[0].text

    def test_fetch_for_question_respects_max_fetches_limit(self):
        """Stops fetching when max_fetches limit is reached."""
        mock_fetcher = MagicMock()
        mock_fetcher.search.return_value = [
            {"url": f"https://example.com/{i}"} for i in range(100)
        ]
        mock_fetcher.cache_check.return_value = None
        mock_fetcher.fetch.return_value = {"content": "Content", "url": "https://example.com"}

        engine = WebResearchEngine(fetcher=mock_fetcher, cost_limit_usd=0.01)
        context = engine.fetch_for_question("test question", max_queries=1)

        # Should not exceed max_fetches (around 10 for $0.01)
        assert len(context.web_findings) <= engine._max_fetches
