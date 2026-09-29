"""Shared helper both `CodexAdapter` and `ClaudeAdapter` use to declare
AgentFlow's own MCP server (`app/mcp/server.py`) to the CLI they spawn, when
a session opted in via `options["mcp_tools"]` at `start()` (docs/AGENT_ADAPTER.md,
MCP tool bridge section).

The declaration is always the same "one Python subprocess, scoped to one
project" triple; only how each CLI is told about it differs -- Claude reads
a `--mcp-config` JSON file, Codex takes inline `-c mcp_servers.<name>.*`
overrides (the same dotted-TOML-path mechanism `CodexAdapter._model_flags`/
`_permission_flags` already use, so no config file is needed there).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

_SERVER_NAME = "agentflow"

# `app/agents/mcp_config.py` -> `app/agents` -> `app` -> repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Every tool `app/mcp/server.py` currently registers. Both CLIs gate MCP tool
# calls behind a permission system separate from file-edit/shell permissions
# (confirmed against real `codex`/`claude` binaries: `acceptEdits` and a plain
# `sandbox_mode` override both still block an MCP tool call outright), so each
# adapter needs this list to grant exactly these tools -- extend it here as
# Sprint/Pipeline/Ralph tool modules are added, nowhere else.
TOOL_NAMES = (
    "backlog_create_item",
    "backlog_list_items",
    "backlog_get_item",
    "backlog_update_item",
    "backlog_transition_item",
    "backlog_record_note",
    "backlog_list_history",
)


def database_path(db: sqlite3.Connection) -> str:
    """Recovers the on-disk path backing a connection, so an adapter can tell
    the MCP subprocess where to connect without threading a new field through
    `AgentContext`/`options`."""
    row = db.execute("PRAGMA database_list").fetchone()
    return row[2] if row else ""


def server_command(project_id: int, session_id: int, database_path: str) -> tuple[str, list[str], dict[str, str]]:
    """The MCP server is spawned by the CLI (Codex/Claude), whose own working
    directory is the *target project's* repository (`AgentContext.working_directory`,
    e.g. `/home/devadmin/git/SAM6`), not AgentFlow's install directory -- confirmed
    live: without `PYTHONPATH`, `python -m app.mcp.server` fails with the target
    repo as cwd (`app` isn't importable from there) and the CLI reports the server
    as `"status":"failed"`, silently leaving the agent with no AgentFlow tools at
    all. `PYTHONPATH` makes `-m app.mcp.server`'s module lookup independent of
    whatever cwd the spawning CLI happens to use."""
    command = sys.executable
    args = ["-m", "app.mcp.server", "--project-id", str(project_id)]
    env = {
        "AGENTFLOW_DATABASE_PATH": database_path,
        "AGENTFLOW_MCP_SESSION_ID": str(session_id),
        "PYTHONPATH": str(_REPO_ROOT),
    }
    return command, args, env


def claude_mcp_config_path(project_id: int, session_id: int, database_path: str) -> str:
    """Writes (or refreshes) the `--mcp-config` JSON file for one session and
    returns its path. Stable per `session_id` so `start`/`resume`/`send` all
    reuse the same file instead of leaking a new temp file per turn."""
    command, args, env = server_command(project_id, session_id, database_path)
    payload = {"mcpServers": {_SERVER_NAME: {"command": command, "args": args, "env": env}}}
    path = Path(tempfile.gettempdir()) / "agentflow-mcp" / f"session-{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return str(path)


def claude_allowed_tools_flag() -> str:
    """`--allowedTools=<comma-list>`, scoped to exactly `TOOL_NAMES` so this
    grant covers nothing beyond AgentFlow's own tools -- Edit/Bash/etc. still
    follow whatever `--permission-mode` the session picked. Must be glued
    with `=` (one argv token), not `["--allowedTools", value]`: like
    `--mcp-config`, it's a variadic flag and two separate tokens lets it
    swallow the prompt positional that follows as another "tool name"."""
    names = ",".join(f"mcp__{_SERVER_NAME}__{name}" for name in TOOL_NAMES)
    return f"--allowedTools={names}"


def codex_mcp_flags(project_id: int, session_id: int, database_path: str) -> list[str]:
    """`-c mcp_servers.<name>.*` overrides for `codex exec`/`codex exec resume`,
    plus `--approve-for-me`. `-c`'s value is parsed as TOML (`codex exec
    --help`); TOML array/inline-table syntax is a superset of the JSON syntax
    used here for `args`/`env`.

    Unlike Claude, Codex has no per-tool allowlist for MCP calls -- confirmed
    against the real CLI, a plain `sandbox_mode` override still leaves an MCP
    tool call blocked ("user cancelled MCP tool call") because `codex exec`
    has no terminal to answer the approval prompt. `--approve-for-me` routes
    *all* approval requests for the turn (not just MCP -- also shell commands
    that would otherwise need approval) through Codex's own automatic review
    under the `workspace-write` sandbox, which is narrower than
    `--dangerously-bypass-approvals-and-sandbox` (no sandboxing at all) but
    still broader than Claude's exact-tool grant -- documented in
    docs/AGENT_ADAPTER.md §24."""
    command, args, env = server_command(project_id, session_id, database_path)
    args_toml = json.dumps(args)
    env_toml = "{" + ", ".join(f'{key} = "{value}"' for key, value in env.items()) + "}"
    return [
        "--approve-for-me",
        "-c", f'mcp_servers.{_SERVER_NAME}.command="{command}"',
        "-c", f"mcp_servers.{_SERVER_NAME}.args={args_toml}",
        "-c", f"mcp_servers.{_SERVER_NAME}.env={env_toml}",
    ]
