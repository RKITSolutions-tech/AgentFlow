from __future__ import annotations

import os
import sqlite3
from typing import Any

from app.agents import models
from app.agents.base import AgentAdapter, AgentContext
from app.agents.models import AgentEvent, AgentSession
from app.mcp.tools import backlog as backlog_tools

FAKE_AGENT_VERSION = "fake-1.0"


class FakeAgentAdapter(AgentAdapter):
    """Deterministic adapter used for tests and the Phase 1 vertical slice.

    See docs/AGENT_ADAPTER.md section 16. Behaviour is entirely driven by a
    scripted list of steps passed via `options["script"]` at `start()` time.
    The script and a cursor into it are persisted on the session's metadata
    (docs/AGENT_ADAPTER.md section 7) rather than held in adapter memory, so
    `resume()` can continue a scripted run after a simulated failure using
    only the database, exactly as a real agent's session would be resumed.
    """

    def __init__(self, db: sqlite3.Connection):
        self._db = db

    # -- AgentAdapter interface -------------------------------------------

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return FAKE_AGENT_VERSION

    def capabilities(self) -> frozenset[str]:
        return frozenset({"structured_events", "fork"})

    def discover_sessions(self, project_id: int) -> list[AgentSession]:
        return models.list_agent_sessions_for_project(self._db, project_id)

    def start(
        self, context: AgentContext, prompt: str, options: dict[str, Any] | None = None
    ) -> AgentSession:
        options = options or {}
        script = options.get("script", [])

        session_id = models.create_agent_session(
            self._db,
            project_id=context.project_id,
            agent_type="fake",
            role=options.get("role", "GENERAL"),
            execution_provider=context.execution_provider,
            execution_target=context.execution_target,
            metadata={
                "script": script,
                "cursor": 0,
                "working_directory": context.working_directory,
                "mcp_tools": bool(options.get("mcp_tools")),
            },
        )
        models.set_session_status(self._db, session_id, "RUNNING")
        models.add_agent_event(self._db, session_id, "PromptSubmitted", data=prompt)

        return self._advance(session_id)

    def resume(
        self, session_id: int, prompt: str | None, options: dict[str, Any] | None = None
    ) -> AgentSession:
        if prompt:
            models.add_agent_event(self._db, session_id, "PromptSubmitted", data=prompt)
        models.set_session_status(self._db, session_id, "RUNNING")
        return self._advance(session_id)

    def send(
        self, session_id: int, content: str, options: dict[str, Any] | None = None
    ) -> None:
        models.add_agent_event(self._db, session_id, "PromptSubmitted", data=content)

    def stop(self, session_id: int) -> None:
        models.set_session_status(self._db, session_id, "STOPPED")
        models.add_agent_event(self._db, session_id, "AgentStatus", data="STOPPED")

    def status(self, session_id: int) -> AgentSession:
        return models.get_agent_session(self._db, session_id)

    def stream(self, session_id: int, after_id: int | None = None) -> list[AgentEvent]:
        return models.list_agent_events(self._db, session_id, after_id=after_id)

    # -- scripted execution -------------------------------------------------

    def _advance(self, session_id: int) -> AgentSession:
        session = models.get_agent_session(self._db, session_id)
        script = session.metadata["script"]
        cursor = session.metadata["cursor"]
        working_directory = session.metadata["working_directory"]
        mcp_tools = session.metadata.get("mcp_tools", False)

        while cursor < len(script):
            step = script[cursor]
            action = step["action"]

            if action == "message":
                models.add_agent_event(self._db, session_id, "AgentText", data=step["text"])
            elif action == "ask":
                models.create_clarifying_question(
                    self._db,
                    session_id,
                    step["question"],
                    step["options"],
                    header=step.get("header", ""),
                    multi_select=step.get("multi_select", False),
                )
            elif action == "write_file":
                self._write_fixture_file(working_directory, step["path"], step["content"])
                models.add_agent_event(
                    self._db, session_id, "AgentToolCall", data=f"write_file {step['path']}"
                )
                models.add_agent_event(self._db, session_id, "AgentToolResult", data="ok")
            elif action == "tool_call":
                result = self._call_mcp_tool(session, step["tool"], step.get("args", {}))
                models.add_agent_event(
                    self._db, session_id, "AgentToolCall", data=f"{step['tool']}({step.get('args', {})})"
                )
                models.add_agent_event(self._db, session_id, "AgentToolResult", data=str(result))
            elif action == "fail":
                models.add_agent_event(
                    self._db,
                    session_id,
                    "AgentError",
                    data=step.get("error", "simulated failure"),
                )
                cursor += 1
                models.set_session_metadata(
                    self._db,
                    session_id,
                    {"script": script, "cursor": cursor, "working_directory": working_directory, "mcp_tools": mcp_tools},
                )
                models.set_session_status(self._db, session_id, "FAILED")
                return models.get_agent_session(self._db, session_id)
            elif action == "complete":
                models.add_agent_event(self._db, session_id, "AgentComplete", data="done")
                cursor += 1
                models.set_session_metadata(
                    self._db,
                    session_id,
                    {"script": script, "cursor": cursor, "working_directory": working_directory, "mcp_tools": mcp_tools},
                )
                models.set_session_status(self._db, session_id, "COMPLETED")
                return models.get_agent_session(self._db, session_id)
            else:
                raise ValueError(f"Unknown FakeAgent script action: {action!r}")

            cursor += 1
            models.set_session_metadata(
                self._db,
                session_id,
                {"script": script, "cursor": cursor, "working_directory": working_directory, "mcp_tools": mcp_tools},
            )

        models.set_session_status(self._db, session_id, "COMPLETED")
        return models.get_agent_session(self._db, session_id)

    def _call_mcp_tool(self, session: AgentSession, tool: str, args: dict[str, Any]) -> Any:
        """Calls an `app/mcp/tools/` function directly, in-process, instead of
        going through a real MCP stdio subprocess -- proves the same
        `options["mcp_tools"]` -> tool-call wiring `CodexAdapter`/`ClaudeAdapter`
        use (docs/AGENT_ADAPTER.md, MCP tool bridge section) with a
        deterministic test double, since a scripted `FakeAgentAdapter` run has
        no real CLI to spawn `app/mcp/server.py` as a child process."""
        if not session.metadata.get("mcp_tools"):
            raise ValueError(f"Session {session.id} did not opt into mcp_tools")
        changed_by = f"agent:{session.id}"
        dispatch = {
            "backlog_create_item": lambda: backlog_tools.create_item(
                self._db, session.project_id, created_by=changed_by, **args
            ),
            "backlog_list_items": lambda: backlog_tools.list_items(
                self._db, session.project_id, **args
            ),
            "backlog_get_item": lambda: backlog_tools.get_item(
                self._db, session.project_id, **args
            ),
            "backlog_update_item": lambda: backlog_tools.update_item(
                self._db, session.project_id, **args
            ),
            "backlog_transition_item": lambda: backlog_tools.transition_item(
                self._db, session.project_id, changed_by=changed_by, **args
            ),
            "backlog_record_note": lambda: backlog_tools.record_note(
                self._db, session.project_id, changed_by=changed_by, **args
            ),
            "backlog_list_history": lambda: backlog_tools.list_history(
                self._db, session.project_id, **args
            ),
        }
        if tool not in dispatch:
            raise ValueError(f"Unknown MCP tool {tool!r}")
        return dispatch[tool]()

    def _write_fixture_file(
        self, working_directory: str, relative_path: str, content: str
    ) -> None:
        resolved_dir = os.path.realpath(working_directory)
        target = os.path.realpath(os.path.join(resolved_dir, relative_path))
        if target != resolved_dir and not target.startswith(resolved_dir + os.sep):
            raise ValueError(f"Fixture path {relative_path!r} escapes working directory")
        with open(target, "w") as fh:
            fh.write(content)
