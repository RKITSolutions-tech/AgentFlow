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
from app.config import allowed_roots_from_env
from app.mcp.tools import backlog as backlog_tools
from app.mcp.tools import wiki as wiki_tools
from app.runs.security import extra_patterns_from_env


def build_server(
    pool: mcp_db.ConnectionPool,
    project_id: int,
    changed_by: str,
    allowed_roots: tuple[str, ...] = (),
    redact_patterns: tuple[str, ...] = (),
) -> MCPServer:
    server = MCPServer(
        name="agentflow",
        instructions=(
            f"Tools scoped to AgentFlow project #{project_id}'s Backlog, Sprints, "
            "Pipelines and Ralph loop, plus its wiki knowledge store. Writes go "
            "through the same validation the AgentFlow UI uses (e.g. backlog status "
            "transitions). Search the wiki before answering questions it may cover; "
            "record settled decisions and useful findings with wiki_write."
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

    @server.tool()
    def wiki_list() -> list[dict]:
        """List the wikis (folders of markdown pages) this project can use, with
        each wiki's id and page paths."""
        return wiki_tools.list_wikis(pool.get(), project_id, allowed_roots)

    @server.tool()
    def wiki_search(query: str, wiki_id: int | None = None, limit: int = 20) -> list[dict]:
        """Search wiki pages for all the words in `query` (case-insensitive),
        across every wiki this project can use or just `wiki_id`. Returns the
        best-matching pages with a snippet each."""
        return wiki_tools.search(pool.get(), project_id, allowed_roots, query, wiki_id=wiki_id, limit=limit)

    @server.tool()
    def wiki_read(wiki_id: int, page: str) -> dict[str, Any]:
        """Read one wiki page, e.g. page="index.md" or "decisions/auth.md"."""
        return wiki_tools.read(pool.get(), project_id, allowed_roots, wiki_id, page)

    @server.tool()
    def wiki_write(wiki_id: int, page: str, content: str) -> dict[str, Any]:
        """Create or replace a wiki page (markdown, path ending in .md;
        sub-folders are created). Use it to record decisions, findings and
        context worth keeping from this discussion. Read the page first if it
        exists -- this replaces its whole content. Secrets are redacted."""
        return wiki_tools.write(pool.get(), project_id, allowed_roots, wiki_id, page, content, redact_patterns)

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    args = parser.parse_args()

    database_path = os.environ["AGENTFLOW_DATABASE_PATH"]
    session_id = os.environ.get("AGENTFLOW_MCP_SESSION_ID", "")
    changed_by = f"agent:{session_id}" if session_id else "agent"

    pool = mcp_db.ConnectionPool(database_path)
    server = build_server(
        pool, args.project_id, changed_by,
        allowed_roots=allowed_roots_from_env(), redact_patterns=extra_patterns_from_env(),
    )
    server.run()


if __name__ == "__main__":
    main()
