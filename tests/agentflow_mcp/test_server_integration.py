import json

import pytest

from app.agents import mcp_config
from app.db import get_db

pytest.importorskip("mcp")

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _payload(result):
    """Tool handlers return a bare `dict`/`list[dict]`, which the `mcp` SDK
    doesn't turn into `structured_content` without a more specific return
    annotation -- it ships as a JSON text block instead, same as a real
    agent CLI would receive it."""
    return json.loads(result.content[0].text)


@pytest.fixture
def project_db_path(app):
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        conn.commit()
    return app.config["DATABASE_PATH"]


@pytest.mark.asyncio
async def test_backlog_tools_over_real_mcp_subprocess(project_db_path, tmp_path):
    """Spawns `app/mcp/server.py` as a real stdio subprocess -- the same way
    `CodexAdapter`/`ClaudeAdapter` do via `app/agents/mcp_config.py` -- and
    drives it with the `mcp` client SDK, proving the tool schemas the server
    advertises actually work end to end, not just the wrapped functions.

    Uses `mcp_config.server_command()` itself (not hand-rolled args/env) and
    a `cwd` that is deliberately *not* this repo -- a session's real working
    directory is the target project's repository, never AgentFlow's own, and
    a previous version of this test (cwd defaulting to the repo root) passed
    while the real adapters were silently broken for every real session,
    because `python -m app.mcp.server` only found the `app` package by
    accident of running from AgentFlow's own directory."""
    command, args, env = mcp_config.server_command(1, 42, project_db_path)
    other_repo = tmp_path / "not-agentflow"
    other_repo.mkdir()
    params = StdioServerParameters(command=command, args=args, env=env, cwd=str(other_repo))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert "backlog_create_item" in names
            assert "backlog_transition_item" in names

            created = await session.call_tool(
                "backlog_create_item", {"title": "From chat", "priority": "high"},
            )
            assert created.is_error is not True
            item = _payload(created)
            assert item["title"] == "From chat"
            assert item["created_by"] == "agent:42"

            moved = await session.call_tool(
                "backlog_transition_item", {"item_id": item["id"], "new_status": "TRIAGED"},
            )
            assert _payload(moved)["status"] == "TRIAGED"

            listed = await session.call_tool("backlog_list_items", {})
            # List-returning tools populate structured_content (`{"result": [...]}`)
            # reliably; their single free-text content block collapses a
            # one-element list to just that element, so don't use `_payload` here.
            assert len(listed.structured_content["result"]) == 1
