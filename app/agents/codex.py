from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from app.agents import models
from app.agents.base import AgentAdapter, AgentContext
from app.agents.models import AgentEvent, AgentSession
from app.execution.base import ExecutionProvider
from app.projects import models as project_models

_VERSION_TIMEOUT_SECONDS = 5.0


class CodexAdapter(AgentAdapter):
    """Adapter for the Codex CLI.

    See docs/AGENT_ADAPTER.md section 17. Binary discovery and version
    detection landed in task 4.1; this adapter now also implements session
    lifecycle (task 4.2) by running `codex exec`/`codex exec resume`
    synchronously through the injected ExecutionProvider and parsing the
    captured `--json` stdout for the external session id and final message.
    Prompt/output streaming while a turn is in flight (task 4.3) and
    stop/persisted-history support (task 4.4) still raise NotImplementedError.
    """

    def __init__(
        self,
        db: sqlite3.Connection | None = None,
        execution_provider: ExecutionProvider | None = None,
        binary: str = "codex",
    ):
        self._db = db
        self._execution_provider = execution_provider
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
        return frozenset({"resume", "session_discovery"})

    def discover_sessions(self, project_id: int) -> list[AgentSession]:
        working_directory = self._primary_repository_path(project_id)
        if working_directory is not None:
            self._import_native_sessions(project_id, working_directory)
        return models.list_agent_sessions_for_project(self._db, project_id)

    def start(
        self, context: AgentContext, prompt: str, options: dict[str, Any] | None = None
    ) -> AgentSession:
        options = options or {}

        session_id = models.create_agent_session(
            self._db,
            project_id=context.project_id,
            agent_type="codex",
            role=options.get("role", "GENERAL"),
            execution_provider=context.execution_provider,
            execution_target=context.execution_target,
            metadata={"working_directory": context.working_directory},
        )
        models.set_session_status(self._db, session_id, "RUNNING")
        models.add_agent_event(self._db, session_id, "PromptSubmitted", data=prompt)

        command = [self._binary, "exec", "--json", prompt]
        process = self._execution_provider.execute(
            command, options={"context_id": int(context.execution_target)}
        )
        return self._finish_turn(session_id, process)

    def resume(
        self, session_id: int, prompt: str | None, options: dict[str, Any] | None = None
    ) -> AgentSession:
        session = models.get_agent_session(self._db, session_id)
        if session is None:
            raise ValueError(f"Unknown agent session {session_id}")
        if session.external_session_id is None:
            raise ValueError(
                f"Agent session {session_id} has no Codex session id to resume"
            )

        if prompt:
            models.add_agent_event(self._db, session_id, "PromptSubmitted", data=prompt)
        models.set_session_status(self._db, session_id, "RUNNING")

        command = [self._binary, "exec", "resume", session.external_session_id, "--json"]
        if prompt:
            command.append(prompt)
        process = self._execution_provider.execute(
            command, options={"context_id": int(session.execution_target)}
        )
        return self._finish_turn(session_id, process)

    def send(self, session_id: int, content: str) -> None:
        raise NotImplementedError("Codex prompt submission lands in task 4.3")

    def stop(self, session_id: int) -> None:
        raise NotImplementedError("Codex session stop lands in task 4.4")

    def status(self, session_id: int) -> AgentSession:
        return models.get_agent_session(self._db, session_id)

    def stream(self, session_id: int, after_id: int | None = None) -> list[AgentEvent]:
        raise NotImplementedError("Codex output streaming lands in task 4.3")

    # -- session lifecycle helpers ------------------------------------------

    def _finish_turn(self, session_id: int, process: Any) -> AgentSession:
        events = self._execution_provider.stream_output(process.id)
        stdout_lines = [e.data for e in events if e.stream == "stdout"]

        session_external_id: str | None = None
        last_agent_message: str | None = None

        for line in stdout_lines:
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue

            event_type = payload.get("type")
            if event_type == "session_meta" and session_external_id is None:
                meta = payload.get("payload", {})
                session_external_id = meta.get("session_id")
            elif event_type == "event_msg":
                inner = payload.get("payload", {})
                if inner.get("type") == "task_complete":
                    last_agent_message = inner.get("last_agent_message", "")

        if session_external_id is not None:
            current = models.get_agent_session(self._db, session_id)
            if current.external_session_id is None:
                models.set_external_session_id(self._db, session_id, session_external_id)

        if last_agent_message is not None:
            models.add_agent_event(
                self._db, session_id, "AgentComplete", data=last_agent_message
            )
            models.set_session_status(self._db, session_id, "COMPLETED")
        else:
            stderr_lines = [e.data for e in events if e.stream == "stderr"]
            error_detail = stderr_lines[-1] if stderr_lines else (
                f"codex exec exited with code {process.exit_code}"
            )
            models.add_agent_event(self._db, session_id, "AgentError", data=error_detail)
            models.set_session_status(self._db, session_id, "FAILED")

        return models.get_agent_session(self._db, session_id)

    def _primary_repository_path(self, project_id: int) -> str | None:
        project = project_models.get_project(self._db, project_id)
        if project is None or not project.repositories:
            return None
        return project.repositories[0].path

    def _import_native_sessions(self, project_id: int, working_directory: str) -> None:
        codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        sessions_dir = codex_home / "sessions"
        if not sessions_dir.is_dir():
            return

        known_external_ids = {
            s.external_session_id
            for s in models.list_agent_sessions_for_project(self._db, project_id)
            if s.external_session_id is not None
        }

        for rollout_file in sessions_dir.glob("**/rollout-*.jsonl"):
            try:
                with rollout_file.open() as fh:
                    first_line = fh.readline()
            except OSError:
                continue

            try:
                payload = json.loads(first_line)
            except (json.JSONDecodeError, TypeError):
                continue

            if payload.get("type") != "session_meta":
                continue
            meta = payload.get("payload", {})
            if meta.get("cwd") != working_directory:
                continue

            external_id = meta.get("session_id")
            if external_id is None or external_id in known_external_ids:
                continue

            imported_id = models.create_agent_session(
                self._db,
                project_id=project_id,
                agent_type="codex",
                role="GENERAL",
                execution_provider="host",
                execution_target="",
                metadata={"working_directory": working_directory, "imported": True},
            )
            models.set_external_session_id(self._db, imported_id, external_id)
            models.set_session_status(self._db, imported_id, "COMPLETED")
            known_external_ids.add(external_id)

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
