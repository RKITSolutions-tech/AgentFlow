from __future__ import annotations

import shutil
import subprocess
from typing import Any

from app.agents.base import AgentAdapter, AgentContext
from app.agents.models import AgentEvent, AgentSession

_VERSION_TIMEOUT_SECONDS = 5.0


class CodexAdapter(AgentAdapter):
    """Adapter for the Codex CLI.

    See docs/AGENT_ADAPTER.md section 17. Only binary discovery and version
    detection are implemented so far (task 4.1); session lifecycle, prompt
    streaming and stop/history support land in later subtasks of task 4.
    """

    def __init__(self, binary: str = "codex"):
        self._binary = binary
        self._checked = False
        self._available = False
        self._version: str | None = None

    # -- AgentAdapter interface -------------------------------------------

    def available(self) -> bool:
        self._detect()
        return self._available

    def version(self) -> str:
        self._detect()
        return self._version or ""

    def capabilities(self) -> frozenset[str]:
        return frozenset()

    def discover_sessions(self, project_id: int) -> list[AgentSession]:
        raise NotImplementedError("Codex session discovery lands in task 4.2")

    def start(
        self, context: AgentContext, prompt: str, options: dict[str, Any] | None = None
    ) -> AgentSession:
        raise NotImplementedError("Codex session start lands in task 4.2")

    def resume(
        self, session_id: int, prompt: str | None, options: dict[str, Any] | None = None
    ) -> AgentSession:
        raise NotImplementedError("Codex session resume lands in task 4.2")

    def send(self, session_id: int, content: str) -> None:
        raise NotImplementedError("Codex prompt submission lands in task 4.3")

    def stop(self, session_id: int) -> None:
        raise NotImplementedError("Codex session stop lands in task 4.4")

    def status(self, session_id: int) -> AgentSession:
        raise NotImplementedError("Codex session status lands in task 4.2")

    def stream(self, session_id: int, after_id: int | None = None) -> list[AgentEvent]:
        raise NotImplementedError("Codex output streaming lands in task 4.3")

    # -- detection ----------------------------------------------------------

    def _detect(self) -> None:
        if self._checked:
            return
        self._checked = True

        path = shutil.which(self._binary)
        if path is None:
            return

        try:
            result = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return

        if result.returncode != 0:
            return

        output = (result.stdout or "").strip() or (result.stderr or "").strip()
        if not output:
            return

        self._available = True
        self._version = output.split()[-1]
