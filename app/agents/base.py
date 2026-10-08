from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.agents.models import AgentEvent, AgentSession

# The canonical role names a skill can target (docs/AGENT_ADAPTER.md §23.2). `role`
# itself stays a plain string everywhere it's threaded through `options` today;
# this tuple is only used to validate/filter skill bindings and populate the UI.
AGENT_ROLES = ("GENERAL", "PLANNING", "IMPLEMENTATION", "RESEARCH", "VERIFICATION")


@dataclass
class AgentContext:
    """Working context passed to an adapter when starting or resuming a session.

    See docs/AGENT_ADAPTER.md section 14.
    """

    project_id: int
    working_directory: str
    execution_provider: str = "host"
    execution_target: str = ""
    model: str | None = None


class WebFetcher(ABC):
    """Common interface for fetching and caching web content (docs/AGENT_ADAPTER.md §23 "Web research").

    Implementations handle URL fetching, search queries, and cache lookups for
    research sessions. Each method is idempotent and non-blocking; errors raise
    SecurityError (blocked URLs, invalid schemes) or OperationError (network failures).
    """

    @abstractmethod
    def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Search for URLs matching a query.

        Returns list of {url, title, snippet, relevance_score} dicts, sorted
        by relevance_score descending. Empty list if the query yields no results.

        Raises SecurityError if query format is invalid.
        """

    @abstractmethod
    def fetch(self, url: str) -> dict[str, Any]:
        """Fetch and parse HTML content from a URL.

        Returns {url, content, content_hash, mime_type}; content is plain text
        extracted from HTML, at most 10KB. Raises SecurityError for blocked
        domains or disallowed schemes (file://, etc.). Raises OperationError
        for network timeouts or server errors.
        """

    @abstractmethod
    def cache_check(self, url: str, kind: str = "web_content") -> dict[str, Any] | None:
        """Check if URL has cached content (not expired).

        Returns cached entry {url, content, content_hash, fetched_at, expires_at}
        if found and not stale; None otherwise. kind in ('web_content', 'web_search_result').
        """


class AgentAdapter(ABC):
    """Common interface every coding-agent adapter implements.

    See docs/AGENT_ADAPTER.md section 3. The rest of AgentFlow depends only on
    this contract, never on agent-specific CLI behaviour.
    """

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def version(self) -> str: ...

    @abstractmethod
    def capabilities(self) -> frozenset[str]: ...

    @abstractmethod
    def discover_sessions(self, project_id: int) -> list[AgentSession]: ...

    @abstractmethod
    def start(
        self, context: AgentContext, prompt: str, options: dict[str, Any] | None = None
    ) -> AgentSession: ...

    @abstractmethod
    def resume(
        self, session_id: int, prompt: str | None, options: dict[str, Any] | None = None
    ) -> AgentSession: ...

    @abstractmethod
    def send(
        self, session_id: int, content: str, options: dict[str, Any] | None = None
    ) -> None:
        """Send a follow-up message. ``options["images"]`` lists image file paths
        to attach, for adapters declaring the ``image_input`` capability."""

    @abstractmethod
    def stop(self, session_id: int) -> None: ...

    @abstractmethod
    def status(self, session_id: int) -> AgentSession: ...

    @abstractmethod
    def stream(self, session_id: int, after_id: int | None = None) -> list[AgentEvent]: ...
