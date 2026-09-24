from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.agents.models import AgentEvent, AgentSession


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
    def send(self, session_id: int, content: str) -> None: ...

    @abstractmethod
    def stop(self, session_id: int) -> None: ...

    @abstractmethod
    def status(self, session_id: int) -> AgentSession: ...

    @abstractmethod
    def stream(self, session_id: int, after_id: int | None = None) -> list[AgentEvent]: ...
