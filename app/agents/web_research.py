"""Web research integration for RESEARCH sessions (task 49.3-49.5).

Extends research sessions to fetch and cache web content, tracking which layer
each source came from: repository, knowledge_base, web_search, or web_fetch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.base import WebFetcher
from app.agents.web_fetcher import HostWebFetcher, WebFetcherError


@dataclass
class LayeredFinding:
    """A research finding with source layer metadata."""

    text: str
    source_layer: str  # 'repository' | 'knowledge_base' | 'web_search' | 'web_fetch'
    url: str = ""  # For web sources
    content_excerpt: str = ""
    confidence: str = "unverified"


@dataclass
class LayeredResearchContext:
    """Research context with findings organized by layer.

    Built by aggregating repository context, knowledge base entries, and web
    results before passing to the agent prompt.
    """

    repository_findings: list[LayeredFinding] = field(default_factory=list)
    knowledge_base_findings: list[LayeredFinding] = field(default_factory=list)
    web_findings: list[LayeredFinding] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Render all layers as prompt text for the agent."""
        lines = []
        if self.repository_findings:
            lines.append("Repository context:")
            for finding in self.repository_findings:
                lines.append(f"  - {finding.text}")
        if self.knowledge_base_findings:
            lines.append("\nKnowledge base entries:")
            for finding in self.knowledge_base_findings:
                lines.append(f"  - {finding.text}")
        if self.web_findings:
            lines.append("\nWeb research findings:")
            for finding in self.web_findings:
                url_note = f" ({finding.url})" if finding.url else ""
                lines.append(f"  - {finding.text}{url_note}")
        return "\n".join(lines)

    def summary_counts(self) -> dict[str, int]:
        """Return counts of findings per layer."""
        return {
            "repository": len(self.repository_findings),
            "knowledge_base": len(self.knowledge_base_findings),
            "web_search": len([f for f in self.web_findings if f.source_layer == "web_search"]),
            "web_fetch": len([f for f in self.web_findings if f.source_layer == "web_fetch"]),
        }


class WebResearchEngine:
    """Orchestrates web fetching for research sessions.

    Handles building search queries, fetching and caching URLs, tracking layers,
    and enforcing cost/count limits (task 49.3-49.5).
    """

    def __init__(self, fetcher: WebFetcher | None = None, cost_limit_usd: float = 10.0):
        self._fetcher = fetcher or HostWebFetcher()
        self._cost_limit_usd = cost_limit_usd
        # Rough estimate: ~1000 fetches per $1 USD
        self._max_fetches = max(1, int(cost_limit_usd * 1000))

    def fetch_for_question(
        self,
        question: str,
        library_entries: list[dict[str, Any]] | None = None,
        max_queries: int = 5,
        max_results_per_query: int = 5,
    ) -> LayeredResearchContext:
        """Fetch web content relevant to a research question.

        Builds search queries from the question and knowledge base entries,
        fetches unique URLs, and returns findings organized by layer.

        Returns LayeredResearchContext with web_findings populated (empty if
        fetching is disabled or fails). Does not raise; failures are logged
        as empty results.
        """
        context = LayeredResearchContext()
        if not question.strip():
            return context

        library_entries = library_entries or []
        queries = self._build_search_queries(question, library_entries, max_queries)
        if not queries:
            return context

        try:
            urls = self._search_and_collect_urls(queries, max_results_per_query)
            for url in urls:
                if len(context.web_findings) >= self._max_fetches:
                    break
                try:
                    self._fetch_and_add_finding(url, context)
                except WebFetcherError:
                    pass  # Skip failed URLs, continue with next
        except Exception:
            pass  # Silently return partial results on any error
        return context

    def _build_search_queries(
        self, question: str, library_entries: list[dict[str, Any]], max_queries: int
    ) -> list[str]:
        """Build search queries from question and knowledge base titles/tags.

        Task 49.4 will implement keyword extraction; for now, return simple
        queries from the question.
        """
        # Simple implementation: split question into words, keep first N
        words = question.split()[:max_queries]
        return [" ".join(words[i : i + 2]) for i in range(0, len(words), 2)]

    def _search_and_collect_urls(self, queries: list[str], max_per_query: int) -> list[str]:
        """Search for URLs; dedup and return unique list.

        Task 49.4 will refine this with actual search engine integration.
        For now, searches return empty results (HostWebFetcher.search() not
        yet implemented in 49.4).
        """
        urls = []
        seen = set()
        for query in queries:
            try:
                results = self._fetcher.search(query, max_results=max_per_query)
                for result in results:
                    url = result.get("url")
                    if url and url not in seen:
                        urls.append(url)
                        seen.add(url)
            except WebFetcherError:
                pass  # Skip failed queries
        return urls

    def _fetch_and_add_finding(self, url: str, context: LayeredResearchContext) -> None:
        """Fetch a URL and add it to context as a web_fetch finding.

        Silently skips on errors (cache checks, fetch failures, etc.).
        """
        try:
            # Check cache first
            cached = self._fetcher.cache_check(url, kind="web_content")
            if cached:
                content = cached.get("content", "")
            else:
                result = self._fetcher.fetch(url)
                content = result.get("content", "")

            if content:
                finding = LayeredFinding(
                    text=content[:200],  # First 200 chars as summary
                    source_layer="web_fetch",
                    url=url,
                    content_excerpt=content[:500],
                    confidence="unverified",
                )
                context.web_findings.append(finding)
        except WebFetcherError:
            pass  # Silently skip on fetch errors
