"""Entry point for the AgentFlow MCP server: `python -m app.mcp.server
--project-id N`. Spawned as a stdio subprocess by the coding-agent CLI
itself (see `app/agents/mcp_config.py`), one process per chat turn, scoped
to a single project so a session can never address another project's data
even though the wrapped persistence functions take a bare `item_id`.

`AGENTFLOW_DATABASE_PATH` and `AGENTFLOW_MCP_SESSION_ID` are passed as
subprocess environment variables by the launching adapter, mirroring how
`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` reach the Claude CLI for a
`local` catalog model (`app/agents/claude.py`'s `_local_model_env`).
"""
from __future__ import annotations

import argparse
import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from app.mcp import db as mcp_db
from app.mcp.tools import backlog as backlog_tools


def build_server(pool: mcp_db.ConnectionPool, project_id: int, changed_by: str) -> MCPServer:
    server = MCPServer(
        name="agentflow",
        instructions=(
            f"Tools scoped to AgentFlow project #{project_id}'s Backlog, Sprints, "
            "Pipelines and Ralph loop. Writes go through the same validation the "
            "AgentFlow UI uses (e.g. backlog status transitions)."
        ),
    )

    @server.tool()
    def backlog_create_item(title: str = "", text: str = "", priority: str | None = None) -> dict[str, Any]:
        """Create a new Backlog item in this project's INBOX status."""
        return backlog_tools.create_item(
            pool.get(), project_id, title=title, text=text, priority=priority, created_by=changed_by,
        )

    @server.tool()
    def backlog_list_items(
        status: str | None = None,
        priority: str | None = None,
        sprint_id: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List Backlog items in this project, optionally filtered by status/priority/sprint."""
        return backlog_tools.list_items(
            pool.get(), project_id, status=status, priority=priority, sprint_id=sprint_id, limit=limit,
        )

    @server.tool()
    def backlog_get_item(item_id: int) -> dict[str, Any]:
        """Get one Backlog item by id."""
        return backlog_tools.get_item(pool.get(), project_id, item_id)

    @server.tool()
    def backlog_update_item(
        item_id: int,
        title: str | None = None,
        text: str | None = None,
        priority: str | None = None,
        sprint_id: int | None = None,
    ) -> dict[str, Any]:
        """Edit a Backlog item's content fields. Use backlog_transition_item to change status."""
        return backlog_tools.update_item(
            pool.get(), project_id, item_id, title=title, text=text, priority=priority, sprint_id=sprint_id,
        )

    @server.tool()
    def backlog_transition_item(item_id: int, new_status: str, notes: str = "") -> dict[str, Any]:
        """Move a Backlog item to a new status. Only moves allowed from its current
        status succeed (e.g. INBOX -> TRIAGED, not INBOX -> RELEASED)."""
        return backlog_tools.transition_item(
            pool.get(), project_id, item_id, new_status, notes=notes, changed_by=changed_by,
        )

    @server.tool()
    def backlog_record_note(item_id: int, notes: str) -> dict[str, Any]:
        """Append a note to a Backlog item's history without changing its status."""
        return backlog_tools.record_note(pool.get(), project_id, item_id, notes, changed_by=changed_by)

    @server.tool()
    def backlog_list_history(item_id: int) -> list[dict]:
        """List the triage history (status changes and notes) for a Backlog item."""
        return backlog_tools.list_history(pool.get(), project_id, item_id)

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    args = parser.parse_args()

    database_path = os.environ["AGENTFLOW_DATABASE_PATH"]
    session_id = os.environ.get("AGENTFLOW_MCP_SESSION_ID", "")
    changed_by = f"agent:{session_id}" if session_id else "agent"

    pool = mcp_db.ConnectionPool(database_path)
    server = build_server(pool, args.project_id, changed_by)
    server.run()


if __name__ == "__main__":
    main()
