"""Unit tests for WebFetcher implementations (task 49.1)."""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from app.agents.web_fetcher import HostWebFetcher, WebFetcherOperationError, WebFetcherSecurityError


class TestHostWebFetcherValidation:
    """Tests for URL validation and security policies."""

    def test_blocked_domains_default(self):
        """Default blocked domains include localhost and 127.0.0.1."""
        fetcher = HostWebFetcher()
        assert "localhost" in fetcher.blocked_domains
        assert "127.0.0.1" in fetcher.blocked_domains

    def test_blocked_schemes_default(self):
        """Default blocked schemes include file and data."""
        fetcher = HostWebFetcher()
        assert "file" in fetcher.blocked_schemes
        assert "data" in fetcher.blocked_schemes

    def test_validate_url_blocks_localhost(self):
        """URLs to localhost are blocked."""
        fetcher = HostWebFetcher()
        with pytest.raises(WebFetcherSecurityError, match="is blocked"):
            fetcher._validate_url("http://localhost:8000/api")

    def test_validate_url_blocks_127_0_0_1(self):
        """URLs to 127.0.0.1 are blocked."""
        fetcher = HostWebFetcher()
        with pytest.raises(WebFetcherSecurityError, match="is blocked"):
            fetcher._validate_url("http://127.0.0.1:8000/api")

    def test_validate_url_blocks_file_scheme(self):
        """URLs with file:// scheme are blocked."""
        fetcher = HostWebFetcher()
        with pytest.raises(WebFetcherSecurityError, match="Scheme 'file' is not allowed"):
            fetcher._validate_url("file:///etc/passwd")

    def test_validate_url_blocks_data_scheme(self):
        """URLs with data: scheme are blocked."""
        fetcher = HostWebFetcher()
        with pytest.raises(WebFetcherSecurityError, match="Scheme 'data' is not allowed"):
            fetcher._validate_url("data:text/html,<h1>test</h1>")

    def test_validate_url_blocks_wildcard_pattern(self):
        """Wildcard patterns like *.internal are blocked."""
        fetcher = HostWebFetcher(blocked_domains=["*.internal"])
        with pytest.raises(WebFetcherSecurityError, match="matches blocked pattern"):
            fetcher._validate_url("http://example.internal/api")

    def test_validate_url_allows_public_domains(self):
        """Public domains pass validation."""
        fetcher = HostWebFetcher()
        # Should not raise
        fetcher._validate_url("https://example.com/page")
        fetcher._validate_url("https://github.com/anthropics/claude-code")

    def test_validate_url_case_insensitive_domain_check(self):
        """Domain blocking is case-insensitive."""
        fetcher = HostWebFetcher(blocked_domains=["example.com"])
        with pytest.raises(WebFetcherSecurityError, match="is blocked"):
            fetcher._validate_url("https://Example.Com/page")


class TestHostWebFetcherExtractText:
    """Tests for HTML text extraction."""

    def test_extract_text_removes_script_tags(self):
        """Script tags and their content are removed."""
        fetcher = HostWebFetcher()
        html = "<p>Hello</p><script>alert('x')</script><p>World</p>"
        text = fetcher._extract_text(html, "text/html")
        assert "alert" not in text
        assert "Hello" in text
        assert "World" in text

    def test_extract_text_removes_style_tags(self):
        """Style tags are removed."""
        fetcher = HostWebFetcher()
        html = "<p>Text</p><style>.x { color: red; }</style>"
        text = fetcher._extract_text(html, "text/html")
        assert ".x" not in text
        assert "color" not in text
        assert "Text" in text

    def test_extract_text_collapses_whitespace(self):
        """Multiple spaces and newlines are collapsed."""
        fetcher = HostWebFetcher()
        html = "<p>Hello    world\n\n  test</p>"
        text = fetcher._extract_text(html, "text/html")
        assert "    " not in text
        assert "Hello world test" in text

    def test_extract_text_non_html_mimetype(self):
        """Non-HTML content is returned as-is."""
        fetcher = HostWebFetcher()
        text = "Plain text content\nwith newlines"
        result = fetcher._extract_text(text, "text/plain")
        assert result == text

    def test_extract_text_handles_parse_errors(self):
        """Parse errors don't crash; raw text is returned."""
        fetcher = HostWebFetcher()
        # BeautifulSoup is quite forgiving, so this should still work
        html = "<p>Unclosed paragraph<p>Another"
        text = fetcher._extract_text(html, "text/html")
        assert "Unclosed" in text
        assert "Another" in text


class TestHostWebFetcherFetch:
    """Tests for fetch() method."""

    @patch("app.agents.web_fetcher.requests.get")
    def test_fetch_returns_expected_structure(self, mock_get):
        """fetch() returns dict with url, content, content_hash, mime_type."""
        fetcher = HostWebFetcher()
        mock_response = MagicMock()
        mock_response.text = "<p>Test content</p>"
        mock_response.url = "https://example.com/page"
        mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_get.return_value = mock_response

        result = fetcher.fetch("https://example.com/page")

        assert "url" in result
        assert "content" in result
        assert "content_hash" in result
        assert "mime_type" in result
        assert result["url"] == "https://example.com/page"
        assert "Test" in result["content"]

    @patch("app.agents.web_fetcher.requests.get")
    def test_fetch_handles_redirects(self, mock_get):
        """fetch() returns final URL after redirects."""
        fetcher = HostWebFetcher()
        mock_response = MagicMock()
        mock_response.text = "Redirected"
        mock_response.url = "https://final.example.com/page"
        mock_get.return_value = mock_response

        result = fetcher.fetch("https://short.url/x")

        assert result["url"] == "https://final.example.com/page"

    @patch("app.agents.web_fetcher.requests.get")
    def test_fetch_truncates_large_content(self, mock_get):
        """Content larger than MAX_CONTENT_SIZE is truncated."""
        fetcher = HostWebFetcher()
        large_content = "x" * (fetcher.MAX_CONTENT_SIZE + 1000)
        mock_response = MagicMock()
        mock_response.text = large_content
        mock_response.url = "https://example.com/page"
        mock_response.headers = {"Content-Type": "text/html"}
        mock_get.return_value = mock_response

        result = fetcher.fetch("https://example.com/page")

        assert len(result["content"]) <= fetcher.MAX_CONTENT_SIZE

    @patch("app.agents.web_fetcher.requests.get")
    def test_fetch_computes_content_hash(self, mock_get):
        """fetch() computes SHA256 hash of extracted content."""
        fetcher = HostWebFetcher()
        content_html = "<p>Test</p>"
        mock_response = MagicMock()
        mock_response.text = content_html
        mock_response.url = "https://example.com/page"
        mock_response.headers = {"Content-Type": "text/html"}
        mock_get.return_value = mock_response

        result = fetcher.fetch("https://example.com/page")

        # Hash is computed on extracted text, not raw HTML
        extracted = fetcher._extract_text(content_html, "text/html")
        expected_hash = hashlib.sha256(extracted.encode()).hexdigest()
        assert result["content_hash"] == expected_hash

    @patch("app.agents.web_fetcher.requests.get")
    def test_fetch_raises_on_timeout(self, mock_get):
        """fetch() raises WebFetcherOperationError on timeout."""
        import requests

        fetcher = HostWebFetcher()
        mock_get.side_effect = requests.Timeout("Request timed out")

        with pytest.raises(WebFetcherOperationError, match="Request timeout"):
            fetcher.fetch("https://example.com/slow")

    @patch("app.agents.web_fetcher.requests.get")
    def test_fetch_raises_on_http_error(self, mock_get):
        """fetch() raises WebFetcherOperationError on HTTP errors."""
        import requests

        fetcher = HostWebFetcher()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        mock_get.return_value = mock_response

        with pytest.raises(WebFetcherOperationError, match="Request failed"):
            fetcher.fetch("https://example.com/notfound")

    def test_fetch_validates_url_before_requesting(self):
        """fetch() validates URL security before making request."""
        fetcher = HostWebFetcher()

        with pytest.raises(WebFetcherSecurityError):
            fetcher.fetch("file:///etc/passwd")


class TestHostWebFetcherSearch:
    """Tests for search() method."""

    def test_search_rejects_empty_query(self):
        """search() raises on empty query."""
        fetcher = HostWebFetcher()

        with pytest.raises(WebFetcherSecurityError, match="cannot be empty"):
            fetcher.search("")

    def test_search_returns_empty_list(self):
        """search() returns empty list (task 49.4 will implement)."""
        fetcher = HostWebFetcher()
        result = fetcher.search("how to test web fetcher")
        assert result == []


class TestHostWebFetcherCacheCheck:
    """Tests for cache_check() method."""

    def test_cache_check_returns_none(self):
        """cache_check() returns None (task 49.2 will implement persistence)."""
        fetcher = HostWebFetcher()
        result = fetcher.cache_check("https://example.com/page")
        assert result is None
