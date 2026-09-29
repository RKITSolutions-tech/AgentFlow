"""Internal MCP server bridging interactive chat sessions into AgentFlow's
Backlog/Sprints/Pipelines/Ralph subsystems (docs/AGENT_ADAPTER.md, MCP tool
bridge section).

`app.mcp.server` runs as a stdio subprocess spawned by the coding-agent CLI
itself (Codex/Claude), not inside the Flask process -- see
`app/agents/mcp_config.py` for how each adapter declares it.
"""
