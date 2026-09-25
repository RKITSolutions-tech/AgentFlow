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
from app.agents.questions import extract_questions
from app.execution.base import ExecutionProvider
from app.projects import models as project_models

_VERSION_TIMEOUT_SECONDS = 5.0

# Process states (docs/EXECUTION_PROVIDER.md section 9) that mean a turn's
# subprocess has exited one way or another and will never emit another event.
_TERMINAL_PROCESS_STATUSES = frozenset({"COMPLETED", "FAILED", "STOPPED", "TIMED_OUT", "LOST"})


class CodexAdapter(AgentAdapter):
    """Adapter for the Codex CLI.

    See docs/AGENT_ADAPTER.md section 17. Binary discovery and version
    detection landed in task 4.1; session lifecycle (start/resume against
    `codex exec`/`codex exec resume`) landed in task 4.2. Task 4.3 adds
    prompt submission to an already-resumable session (`send`) and live
    output streaming (`stream`): both a turn launched synchronously by
    `start`/`resume` and one launched in the background by `send` are
    translated from raw `--json` stdout into structured AgentEvents by the
    same incremental `_sync_events` step, driven off the ExecutionProvider's
    own event-position tracking (`stream_output(process_id, after_id)`) so a
    reconnecting caller can resume from where it left off. Task 4.4 adds
    `stop()`. Prompt/reply history is already persisted as AgentEvents
    (`app/agents/models.py`), keyed by session id and timestamp; `stream()`
    is also the history-retrieval path, so no separate schema is needed.
    Task 4.5 finalizes the integration: `capabilities()` declares what this
    adapter type supports (resume, session discovery, structured events) so
    orchestration/UI can adapt (docs/AGENT_ADAPTER.md section 11); if the
    Codex binary can't actually be launched (missing, unauthenticated,
    permissions), `start`/`resume`/`send` report that the same way any other
    turn failure is reported (`AgentError` + `FAILED` session, see
    `_fail_launch`) instead of letting a raw `OSError` escape.
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
        return frozenset({"resume", "session_discovery", "structured_events", "model_selection"})

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
            metadata={"working_directory": context.working_directory, "model": context.model},
        )
        models.set_session_status(self._db, session_id, "RUNNING")
        models.add_agent_event(self._db, session_id, "PromptSubmitted", data=prompt)

        command = [self._binary, "exec", "--json", *self._model_flags(context.model), prompt]
        try:
            process = self._execution_provider.execute(
                command, options={"context_id": int(context.execution_target)}
            )
        except OSError as exc:
            self._fail_launch(session_id, exc)
            return models.get_agent_session(self._db, session_id)
        self._begin_turn(session_id, process.id)
        self._sync_events(session_id)
        return models.get_agent_session(self._db, session_id)

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

        command = [
            self._binary,
            "exec",
            "resume",
            session.external_session_id,
            "--json",
            *self._model_flags(session.metadata.get("model")),
        ]
        if prompt:
            command.append(prompt)
        try:
            process = self._execution_provider.execute(
                command, options={"context_id": int(session.execution_target)}
            )
        except OSError as exc:
            self._fail_launch(session_id, exc)
            return models.get_agent_session(self._db, session_id)
        self._begin_turn(session_id, process.id)
        self._sync_events(session_id)
        return models.get_agent_session(self._db, session_id)

    def send(self, session_id: int, content: str) -> None:
        session = models.get_agent_session(self._db, session_id)
        if session is None:
            raise ValueError(f"Unknown agent session {session_id}")
        if session.status == "STOPPED":
            raise ValueError(f"Agent session {session_id} has been stopped")
        if session.external_session_id is None:
            raise ValueError(
                f"Agent session {session_id} has no Codex session id to resume"
            )
        if self._turn_in_progress(session):
            raise ValueError(
                f"Agent session {session_id} already has a Codex turn in progress"
            )

        models.add_agent_event(self._db, session_id, "PromptSubmitted", data=content)
        models.set_session_status(self._db, session_id, "RUNNING")

        command = [
            self._binary,
            "exec",
            "resume",
            session.external_session_id,
            "--json",
            *self._model_flags(session.metadata.get("model")),
            content,
        ]
        try:
            process = self._execution_provider.start_process(
                command, options={"context_id": int(session.execution_target)}
            )
        except OSError as exc:
            self._fail_launch(session_id, exc)
            return
        self._begin_turn(session_id, process.id)

    def stop(self, session_id: int) -> None:
        """Terminates any in-flight turn and marks the session STOPPED.

        `resume()` remains valid afterwards (it is how a stopped session is
        deliberately reopened); it is `send()` that must reject further
        input once a session is STOPPED.
        """
        session = models.get_agent_session(self._db, session_id)
        if session is None:
            raise ValueError(f"Unknown agent session {session_id}")

        if self._turn_in_progress(session):
            self._execution_provider.stop_process(session.metadata["process_id"])
            metadata = dict(session.metadata)
            metadata["turn_finalized"] = True
            models.set_session_metadata(self._db, session_id, metadata)

        models.set_session_status(self._db, session_id, "STOPPED")
        models.add_agent_event(self._db, session_id, "AgentStatus", data="STOPPED")

    def status(self, session_id: int) -> AgentSession:
        return models.get_agent_session(self._db, session_id)

    def stream(self, session_id: int, after_id: int | None = None) -> list[AgentEvent]:
        self._sync_events(session_id)
        return models.list_agent_events(self._db, session_id, after_id=after_id)

    # -- turn lifecycle helpers ----------------------------------------------

    def _fail_launch(self, session_id: int, exc: OSError) -> None:
        """Records a graceful failure when the Codex CLI itself can't be launched.

        `ExecutionProvider.execute`/`start_process` re-raise `OSError` (e.g.
        the binary going missing between `available()` and launch, or a
        permissions problem) rather than producing a Process to poll, so
        this is the one failure mode `_sync_events` can never observe. It is
        reported the same way any other turn failure is: an `AgentError`
        event and a `FAILED` session, rather than letting a raw `OSError`
        escape the adapter.
        """
        models.add_agent_event(
            self._db, session_id, "AgentError", data=f"Codex CLI is not available: {exc}"
        )
        models.set_session_status(self._db, session_id, "FAILED")

    def _begin_turn(self, session_id: int, process_id: int) -> None:
        """Records the process backing a newly launched turn.

        Resets the per-turn translation cursor so `_sync_events` starts
        reading this process's output from the beginning, regardless of
        whether the previous turn's process id is still around.
        """
        metadata = dict(models.get_agent_session(self._db, session_id).metadata)
        metadata["process_id"] = process_id
        metadata["process_event_cursor"] = None
        metadata["last_stderr_line"] = None
        metadata["turn_finalized"] = False
        models.set_session_metadata(self._db, session_id, metadata)

    def _turn_in_progress(self, session: AgentSession) -> bool:
        process_id = session.metadata.get("process_id")
        if process_id is None or session.metadata.get("turn_finalized"):
            return False
        process = self._execution_provider.process_status(process_id)
        return process is not None and process.status not in _TERMINAL_PROCESS_STATUSES

    def _sync_events(self, session_id: int) -> None:
        """Translates newly available `--json` stdout lines into AgentEvents.

        Safe to call repeatedly (from `stream()` polling, or right after a
        synchronous `start`/`resume` call): the per-session
        `process_event_cursor` means each raw ProcessEvent is only ever
        translated once, and `turn_finalized` stops re-checking a process
        once its outcome has already been recorded.

        Schema note: this parses the `--json` event stream shape emitted by
        the installed Codex CLI (`thread.started` / `item.completed` /
        `turn.completed` / `turn.failed`), which is distinct from the
        `session_meta` rollout-file format `_import_native_sessions` reads
        (that one is Codex's own on-disk transcript format and unaffected by
        this stream's schema).
        """
        session = models.get_agent_session(self._db, session_id)
        metadata = dict(session.metadata)
        process_id = metadata.get("process_id")
        if process_id is None or metadata.get("turn_finalized"):
            return

        cursor = metadata.get("process_event_cursor")
        raw_events = self._execution_provider.stream_output(process_id, after_id=cursor)

        turn_finalized = False
        last_stderr = metadata.get("last_stderr_line")
        last_agent_message = metadata.get("last_agent_message", "")

        for raw in raw_events:
            cursor = raw.id
            if raw.stream == "stderr":
                last_stderr = raw.data
                continue
            if raw.stream != "stdout":
                continue

            payload = self._parse_json_line(raw.data)
            if payload is None:
                continue

            event_type = payload.get("type")
            if event_type == "thread.started":
                if session.external_session_id is None:
                    thread_id = payload.get("thread_id")
                    if thread_id is not None:
                        models.set_external_session_id(self._db, session_id, thread_id)
            elif event_type == "item.completed":
                item = payload.get("item", {})
                item_type = item.get("type")
                if item_type == "agent_message":
                    # Codex has no structured-question tool, so a reply may carry
                    # an explicit question block (app/agents/questions.py).
                    last_agent_message, question_specs = extract_questions(item.get("text", ""))
                    if last_agent_message or not question_specs:
                        models.add_agent_event(
                            self._db, session_id, "AgentText", data=last_agent_message
                        )
                    for spec in question_specs:
                        models.create_clarifying_question(self._db, session_id, **spec)
                elif item_type == "error":
                    models.add_agent_event(
                        self._db, session_id, "AgentError", data=item.get("message", "")
                    )
            elif event_type == "turn.completed":
                models.add_agent_event(
                    self._db, session_id, "AgentComplete", data=last_agent_message
                )
                models.set_session_status(self._db, session_id, "COMPLETED")
                turn_finalized = True
            elif event_type == "turn.failed":
                error_detail = (payload.get("error") or {}).get("message") or last_stderr or (
                    "codex exec turn failed"
                )
                models.add_agent_event(self._db, session_id, "AgentError", data=error_detail)
                models.set_session_status(self._db, session_id, "FAILED")
                turn_finalized = True

        metadata["process_event_cursor"] = cursor
        metadata["last_stderr_line"] = last_stderr
        metadata["last_agent_message"] = last_agent_message

        if turn_finalized:
            metadata["turn_finalized"] = True
        else:
            process = self._execution_provider.process_status(process_id)
            if process is not None and process.status in _TERMINAL_PROCESS_STATUSES:
                error_detail = last_stderr or f"codex exec exited with code {process.exit_code}"
                models.add_agent_event(self._db, session_id, "AgentError", data=error_detail)
                models.set_session_status(self._db, session_id, "FAILED")
                metadata["turn_finalized"] = True

        models.set_session_metadata(self._db, session_id, metadata)

    @staticmethod
    def _model_flags(model: str | None) -> list[str]:
        return ["-m", model] if model else []

    @staticmethod
    def _parse_json_line(line: str) -> dict[str, Any] | None:
        try:
            return json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return None

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
