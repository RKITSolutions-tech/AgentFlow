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
from app.settings import models as settings_models

# Modes a user may pick per session. `bypassPermissions` is deliberately not
# offered from the UI (mirrors CodexAdapter withholding `danger-full-access`).
PERMISSION_MODES = ("default", "plan", "acceptEdits")

# Env vars used to point the Claude CLI at a local, Anthropic-API-compatible
# server (LM Studio, a proxy, etc.) for a "local" catalog model, mirroring
# CodexAdapter's `model_providers.local` override. The key never appears on
# the command line, so it needs no command-line redaction.
_LOCAL_MODEL_BASE_URL_ENV = "ANTHROPIC_BASE_URL"
_LOCAL_MODEL_AUTH_ENV = "ANTHROPIC_AUTH_TOKEN"

_VERSION_TIMEOUT_SECONDS = 5.0

# Process states (docs/EXECUTION_PROVIDER.md section 9) that mean a turn's
# subprocess has exited one way or another and will never emit another event.
_TERMINAL_PROCESS_STATUSES = frozenset({"COMPLETED", "FAILED", "STOPPED", "TIMED_OUT", "LOST"})


class ClaudeAdapter(AgentAdapter):
    """Adapter for the Claude Code CLI (docs/AGENT_ADAPTER.md section 2).

    Mirrors CodexAdapter's structure: turns run through `claude -p
    --output-format stream-json --verbose`, each `--json`-style stdout line is
    translated into AgentEvents by `_sync_events`, and a turn's outcome is the
    terminal `result` event. Unlike Codex, Claude Code has a native
    structured-question tool (`AskUserQuestion`); a clarifying question
    arrives as a `tool_use` content block rather than text needing parsing
    (app/agents/questions.py), so `_extract_claude_questions` reads it
    directly instead of scanning prose for a fenced block.
    """

    def __init__(
        self,
        db: sqlite3.Connection | None = None,
        execution_provider: ExecutionProvider | None = None,
        binary: str = "claude",
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
        return frozenset(
            {
                "resume",
                "session_discovery",
                "structured_events",
                "model_selection",
                "token_usage",
                "permission_modes",
            }
        )

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
            agent_type="claude",
            role=options.get("role", "GENERAL"),
            execution_provider=context.execution_provider,
            execution_target=context.execution_target,
            metadata={"working_directory": context.working_directory, "model": context.model},
        )
        models.set_session_status(self._db, session_id, "RUNNING")
        models.add_agent_event(self._db, session_id, "PromptSubmitted", data=prompt)

        command = [
            self._binary,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            *self._model_flags(context.model),
            prompt,
        ]
        exec_options: dict[str, Any] = {"context_id": int(context.execution_target)}
        env = self._local_model_env(context.model)
        if env:
            exec_options["environment"] = env
        try:
            process = self._execution_provider.execute(command, options=exec_options)
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
                f"Agent session {session_id} has no Claude session id to resume"
            )

        if prompt:
            models.add_agent_event(self._db, session_id, "PromptSubmitted", data=prompt)
        models.set_session_status(self._db, session_id, "RUNNING")

        command = [
            self._binary,
            "--resume", session.external_session_id,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            *self._model_flags(session.metadata.get("model")),
            *self._permission_flags(session.metadata.get("permission_mode")),
        ]
        if prompt:
            command.append(prompt)
        exec_options: dict[str, Any] = {"context_id": int(session.execution_target)}
        env = self._local_model_env(session.metadata.get("model"))
        if env:
            exec_options["environment"] = env
        try:
            process = self._execution_provider.execute(command, options=exec_options)
        except OSError as exc:
            self._fail_launch(session_id, exc)
            return models.get_agent_session(self._db, session_id)
        self._begin_turn(session_id, process.id)
        self._sync_events(session_id)
        return models.get_agent_session(self._db, session_id)

    def send(
        self, session_id: int, content: str, options: dict[str, Any] | None = None
    ) -> None:
        session = models.get_agent_session(self._db, session_id)
        if session is None:
            raise ValueError(f"Unknown agent session {session_id}")
        if session.status == "STOPPED":
            raise ValueError(f"Agent session {session_id} has been stopped")
        if session.external_session_id is None:
            raise ValueError(
                f"Agent session {session_id} has no Claude session id to resume"
            )
        if self._turn_in_progress(session):
            raise ValueError(
                f"Agent session {session_id} already has a Claude turn in progress"
            )

        models.add_agent_event(self._db, session_id, "PromptSubmitted", data=content)
        models.set_session_status(self._db, session_id, "RUNNING")

        command = [
            self._binary,
            "--resume", session.external_session_id,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            *self._model_flags(session.metadata.get("model")),
            *self._permission_flags(session.metadata.get("permission_mode")),
            content,
        ]
        exec_options: dict[str, Any] = {"context_id": int(session.execution_target)}
        env = self._local_model_env(session.metadata.get("model"))
        if env:
            exec_options["environment"] = env
        try:
            process = self._execution_provider.start_process(command, options=exec_options)
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
        """Records a graceful failure when the Claude CLI itself can't be launched.

        `ExecutionProvider.execute`/`start_process` re-raise `OSError` (e.g.
        the binary going missing between `available()` and launch, or a
        permissions problem) rather than producing a Process to poll, so
        this is the one failure mode `_sync_events` can never observe.
        """
        models.add_agent_event(
            self._db, session_id, "AgentError", data=f"Claude CLI is not available: {exc}"
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
        """Translates newly available `stream-json` stdout lines into AgentEvents.

        Safe to call repeatedly (from `stream()` polling, or right after a
        synchronous `start`/`resume` call): the per-session
        `process_event_cursor` means each raw ProcessEvent is only ever
        translated once, and `turn_finalized` stops re-checking a process
        once its outcome has already been recorded.

        Schema note: this parses the Claude CLI's `stream-json` event shape
        (`system`/`init`, `assistant` message content blocks, terminal
        `result`), which is distinct from the on-disk transcript format under
        `~/.claude/projects/` that `_import_native_sessions` reads.
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
            if event_type == "system":
                if payload.get("subtype") == "init" and session.external_session_id is None:
                    ext_id = payload.get("session_id")
                    if ext_id is not None:
                        models.set_external_session_id(self._db, session_id, ext_id)
            elif event_type == "assistant":
                message = payload.get("message") or {}
                for block in message.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text":
                        text = block.get("text", "")
                        if text:
                            last_agent_message = text
                            models.add_agent_event(self._db, session_id, "AgentText", data=text)
                    elif block_type == "tool_use" and block.get("name") == "AskUserQuestion":
                        for spec in self._extract_claude_questions(block):
                            try:
                                models.create_clarifying_question(self._db, session_id, **spec)
                            except ValueError:
                                continue
            elif event_type == "result":
                self._record_usage(metadata, payload.get("usage"))
                if payload.get("is_error"):
                    error_detail = (
                        payload.get("result") or last_stderr or "claude CLI turn failed"
                    )
                    models.add_agent_event(self._db, session_id, "AgentError", data=error_detail)
                    models.set_session_status(self._db, session_id, "FAILED")
                else:
                    result_text = payload.get("result") or last_agent_message
                    models.add_agent_event(
                        self._db, session_id, "AgentComplete", data=result_text
                    )
                    models.set_session_status(self._db, session_id, "COMPLETED")
                turn_finalized = True

        metadata["process_event_cursor"] = cursor
        metadata["last_stderr_line"] = last_stderr
        metadata["last_agent_message"] = last_agent_message

        if turn_finalized:
            metadata["turn_finalized"] = True
        else:
            process = self._execution_provider.process_status(process_id)
            if process is not None and process.status in _TERMINAL_PROCESS_STATUSES:
                error_detail = last_stderr or f"claude CLI exited with code {process.exit_code}"
                models.add_agent_event(self._db, session_id, "AgentError", data=error_detail)
                models.set_session_status(self._db, session_id, "FAILED")
                metadata["turn_finalized"] = True

        models.set_session_metadata(self._db, session_id, metadata)

    @staticmethod
    def _record_usage(metadata: dict[str, Any], usage: Any) -> None:
        """Add a turn's token counts to the session's running total."""
        if not isinstance(usage, dict):
            return
        total = dict(metadata.get("usage") or {})
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                total[key] = int(total.get(key, 0)) + int(value)
        metadata["usage"] = total

    @staticmethod
    def _extract_claude_questions(block: dict[str, Any]) -> list[dict[str, Any]]:
        """Reads an `AskUserQuestion` tool_use block into `create_clarifying_question` kwargs."""
        input_ = block.get("input")
        if not isinstance(input_, dict):
            return []
        questions = input_.get("questions")
        if not isinstance(questions, list):
            return []
        specs = []
        for q in questions:
            if not isinstance(q, dict):
                continue
            question_text = q.get("question")
            options = q.get("options")
            if not question_text or not isinstance(options, list):
                continue
            specs.append(
                {
                    "question": question_text,
                    "options": options,
                    "header": str(q.get("header") or ""),
                    "multi_select": bool(q.get("multiSelect", False)),
                    "external_id": block.get("id"),
                }
            )
        return specs

    def _model_flags(self, model: str | None) -> list[str]:
        return ["--model", model] if model else []

    def _local_model_entry(self, model: str) -> settings_models.ModelCatalogEntry | None:
        if self._db is None:
            return None
        return next(
            (
                entry
                for entry in settings_models.list_models(self._db, provider="local")
                if entry.model_id == model
            ),
            None,
        )

    def _local_model_env(self, model: str | None) -> dict[str, str]:
        if not model:
            return {}
        local = self._local_model_entry(model)
        if local is None:
            return {}
        env = {_LOCAL_MODEL_BASE_URL_ENV: local.base_url}
        if local.api_key:
            env[_LOCAL_MODEL_AUTH_ENV] = local.api_key
        return env

    @staticmethod
    def _permission_flags(mode: str | None) -> list[str]:
        if mode not in PERMISSION_MODES:
            return []
        return ["--permission-mode", mode]

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
        claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
        projects_dir = claude_home / "projects"
        if not projects_dir.is_dir():
            return

        known_external_ids = {
            s.external_session_id
            for s in models.list_agent_sessions_for_project(self._db, project_id)
            if s.external_session_id is not None
        }

        for session_file in projects_dir.glob("*/*.jsonl"):
            try:
                with session_file.open() as fh:
                    first_line = fh.readline()
            except OSError:
                continue

            try:
                payload = json.loads(first_line)
            except (json.JSONDecodeError, TypeError):
                continue

            if payload.get("cwd") != working_directory:
                continue

            external_id = payload.get("sessionId") or session_file.stem
            if external_id in known_external_ids:
                continue

            imported_id = models.create_agent_session(
                self._db,
                project_id=project_id,
                agent_type="claude",
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
        self._version = output.split()[0]
