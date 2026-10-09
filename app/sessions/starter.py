"""Starting an interactive GENERAL session: one place that builds the starting
prompt (topic, wiki, warm-start, extra and skill context) and records what was
injected. Used by the session-start form (app/sessions/routes.py) and by
item-scoped discussions (app/backlog/discussion.py)."""
from __future__ import annotations

import sqlite3

from app.agents.base import AgentContext
from app.agents.claude import ClaudeAdapter
from app.agents.codex import CodexAdapter
from app.agents.fake import FakeAgentAdapter
from app.agents.models import PLACEHOLDER_PROMPT, set_session_topic, update_session_metadata
from app.execution.host import HostExecutionProvider
from app.knowledge import wiki_sources
from app.prompts import assembler as prompt_assembler
from app.runs.models import now
from app.sessions import chat as chat_module
from app.sessions import topics as topic_models


def start_interactive_session(
    app_config,
    db: sqlite3.Connection,
    project_id: int,
    working_directory: str,
    agent_type: str,
    model: str | None = None,
    mcp_tools: bool = False,
    warm_start: bool = False,
    wiki_context: bool = False,
    topic_id: int | None = None,
    extra_context: str = "",
    extra_metadata: dict | None = None,
    script: list | None = None,
) -> int:
    """Start the session and return its id. `extra_context` goes right after
    any Topic context (it is caller-specific, e.g. the Backlog item under
    discussion); `extra_metadata` is merged into the session metadata;
    `script` drives the fake adapter (tests)."""
    allowed_roots = app_config["ALLOWED_PROJECT_ROOTS"]
    execution_provider = HostExecutionProvider(app_config["DATABASE_PATH"], allowed_roots)
    exec_context = execution_provider.create_context(
        {"working_directory": working_directory, "environment": {}, "target": ""}
    )
    if agent_type == "codex":
        adapter = CodexAdapter(db=db, execution_provider=execution_provider)
    elif agent_type == "claude":
        adapter = ClaudeAdapter(db=db, execution_provider=execution_provider)
    else:
        adapter = FakeAgentAdapter(db=db)
    context = AgentContext(
        project_id=project_id,
        working_directory=working_directory,
        execution_provider="host",
        execution_target=str(exec_context.id),
        model=model if agent_type in ("codex", "claude") else None,
    )

    skills_text, skill_names = prompt_assembler.skill_context(
        db, role="GENERAL", agent_type=agent_type, project_id=project_id,
    )
    warm_start_context = chat_module.get_session_context(db, project_id) if warm_start else ""
    wiki_text = wiki_sources.session_context(db, allowed_roots, project_id) if wiki_context else ""
    # Topic context first: it is the narrowest, most relevant starting point
    # (docs/SESSION_TOPICS.md §5.1).
    topic = topic_models.get_topic(db, topic_id) if topic_id else None
    topic_context = topic_models.get_topic_context(db, topic_id) if topic_id else ""
    preamble = "\n\n".join(
        p for p in (topic_context, extra_context, wiki_text, warm_start_context, skills_text) if p
    )
    initial_prompt = f"{preamble}\n\n{PLACEHOLDER_PROMPT}" if preamble else PLACEHOLDER_PROMPT

    options = {"mcp_tools": mcp_tools}
    if script is not None:
        options["script"] = script
    session = adapter.start(context, initial_prompt, options=options)
    if topic_id:
        set_session_topic(db, session.id, topic_id)
    # Compliance/audit trail (docs/AGENT_ADAPTER.md §23.2, task 53): interactive
    # sessions never pass through execution_prompts, so the injected skill
    # manifest is recorded on the session itself instead.
    update_session_metadata(
        db, session.id,
        injected_context={
            "role": "GENERAL", "agent_type": agent_type, "skills": skill_names,
            "mcp_tools": mcp_tools, "warm_start": warm_start,
            "wiki_context": bool(wiki_text), "assembled_at": now(),
            "topic_id": topic_id, "topic_name": topic.name if topic else None,
            "topic_context": bool(topic_context), "extra_context": bool(extra_context),
        },
        **(extra_metadata or {}),
    )
    return session.id
