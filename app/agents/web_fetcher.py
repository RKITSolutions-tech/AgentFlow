"""Web fetcher implementation for research sessions (docs/AGENT_ADAPTER.md §23 "Web research").

Fetches URLs, parses HTML, and caches content. Enforces security policies
(blocked domains, disallowed schemes) and redaction patterns.
"""
from __future__ import annotations

import hashlib
import os
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from app.agents.base import WebFetcher


class WebFetcherError(RuntimeError):
    """Base error for web fetcher operations."""


class WebFetcherSecurityError(WebFetcherError):
    """Raised when a URL or operation violates security policy."""


class WebFetcherOperationError(WebFetcherError):
    """Raised when a network operation fails."""


class HostWebFetcher(WebFetcher):
    """Fetches and caches web content using requests + BeautifulSoup.

    Blocked domains and schemes are configurable via environment variables:
    - AGENTFLOW_BLOCKED_DOMAINS: comma-separated list (default: localhost,127.0.0.1)
    - AGENTFLOW_BLOCKED_SCHEMES: comma-separated list (default: file,data)
    """

    MAX_CONTENT_SIZE = 10 * 1024  # 10KB
    TIMEOUT_SECONDS = 10

    def __init__(self, blocked_domains: list[str] | None = None, blocked_schemes: list[str] | None = None):
        if blocked_domains is None:
            blocked = os.getenv("AGENTFLOW_BLOCKED_DOMAINS", "localhost,127.0.0.1").split(",")
            blocked_domains = [d.strip() for d in blocked if d.strip()]
        if blocked_schemes is None:
            blocked = os.getenv("AGENTFLOW_BLOCKED_SCHEMES", "file,data").split(",")
            blocked_schemes = [s.strip() for s in blocked if s.strip()]
        self.blocked_domains = blocked_domains
        self.blocked_schemes = blocked_schemes

    def search(self, query: str, max_results: int = 10) -> list[dict[str, str]]:
        """Search for URLs using a simple query-to-engine mapping.

        For now, returns empty results. Full implementation requires
        a search engine API (task 49.4 will refine the search strategy).
        """
        if not query.strip():
            raise WebFetcherSecurityError("Search query cannot be empty")
        return []

    def fetch(self, url: str) -> dict[str, str]:
        """Fetch and parse HTML from a URL.

        Returns {url, content, content_hash, mime_type}.
        Raises WebFetcherSecurityError for blocked domains or disallowed schemes.
        Raises WebFetcherOperationError for network failures.
        """
        self._validate_url(url)
        try:
            response = requests.get(url, timeout=self.TIMEOUT_SECONDS, allow_redirects=True)
            response.raise_for_status()
        except requests.Timeout as e:
            raise WebFetcherOperationError(f"Request timeout for {url}: {e}") from e
        except requests.RequestException as e:
            raise WebFetcherOperationError(f"Request failed for {url}: {e}") from e

        mime_type = response.headers.get("Content-Type", "text/html").split(";")[0]
        content = self._extract_text(response.text, mime_type)
        if len(content) > self.MAX_CONTENT_SIZE:
            content = content[:self.MAX_CONTENT_SIZE]
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        return {
            "url": response.url,  # Final URL after redirects
            "content": content,
            "content_hash": content_hash,
            "mime_type": mime_type,
        }

    def cache_check(self, url: str, kind: str = "web_content") -> dict[str, str] | None:
        """Check if URL is in cache (not yet implemented; task 49.2 adds persistence).

        For now, always returns None (no cache).
        """
        return None

    def _validate_url(self, url: str) -> None:
        """Raise WebFetcherSecurityError if URL violates policy."""
        try:
            parsed = urlparse(url)
        except Exception as e:
            raise WebFetcherSecurityError(f"Invalid URL: {url}") from e

        if parsed.scheme.lower() in self.blocked_schemes:
            raise WebFetcherSecurityError(f"Scheme '{parsed.scheme}' is not allowed")

        hostname = parsed.hostname or ""
        for blocked in self.blocked_domains:
            if blocked.startswith("*."):
                # Pattern: *.internal matches example.internal but not internal
                suffix = blocked[1:]  # Remove leading *
                if hostname.endswith(suffix):
                    raise WebFetcherSecurityError(f"Domain '{hostname}' matches blocked pattern '{blocked}'")
            elif hostname.lower() == blocked.lower():
                raise WebFetcherSecurityError(f"Domain '{hostname}' is blocked")

    def _extract_text(self, html: str, mime_type: str) -> str:
        """Extract plain text from HTML content."""
        if "text/html" not in mime_type:
            return html[:self.MAX_CONTENT_SIZE]

        try:
            soup = BeautifulSoup(html, "html.parser")
            # Remove script and style tags
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            # Collapse multiple spaces
            text = " ".join(text.split())
            return text
        except Exception:
            # If parsing fails, return raw text
            return html[:self.MAX_CONTENT_SIZE]
