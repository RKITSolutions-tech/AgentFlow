"""Chat history (docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md, tasks 61/63/64/69/70).

Deliberately not a parallel message store: every adapter already writes developer
prompts and agent replies into `agent_events` via the one chokepoint
`app.agents.models.add_agent_event` (which now also redacts), and
`agent_sessions.role` already distinguishes developer-initiated sessions (``GENERAL``,
started from `app/sessions/routes.py`) from operational ones (``IMPLEMENTATION`` for
Ralph/pipeline AGENT steps, ``PLANNING`` for sprint planning, ``RESEARCH`` for pipeline
research steps) -- exactly the split the design doc's §3.2 exclusion rules need. This
module is a read-side view over those two tables.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.agents.models import get_question_by_event_id

# event_type -> (role, message_type). AgentStatus (session bookkeeping) and
# AgentComplete (its text duplicates the AgentText already shown for the turn) carry
# no new conversational content, so they are not chat messages.
_EVENT_TYPE_MAP: dict[str, tuple[str, str]] = {
    "PromptSubmitted": ("developer", "text"),
    "AgentText": ("agent", "text"),
    "AgentToolCall": ("agent", "tool"),
    "AgentToolResult": ("agent", "tool"),
    "AgentError": ("system", "error"),
    "ClarifyingQuestion": ("agent", "ask"),
}


@dataclass
class ChatMessage:
    event_id: int
    session_id: int
    agent_type: str
    role: str  # developer | agent | system
    content: str
    message_type: str  # text | tool | ask | error
    redacted: bool
    created_at: str
    question: dict[str, Any] | None = None


def is_chat_session_role(role: str) -> bool:
    """Only ``GENERAL`` sessions are developer-initiated chat (design doc §3.2)."""
    return role == "GENERAL"


def _row_to_message(db: sqlite3.Connection, row: sqlite3.Row) -> ChatMessage | None:
    mapped = _EVENT_TYPE_MAP.get(row["event_type"])
    if mapped is None:
        return None
    role, message_type = mapped
    content = row["data"]
    question = None
    if message_type == "ask":
        q = get_question_by_event_id(db, row["id"])
        if q is not None:
            content = q.question
            question = q.to_dict()
    return ChatMessage(
        event_id=row["id"],
        session_id=row["session_id"],
        agent_type=row["agent_type"],
        role=role,
        content=content,
        message_type=message_type,
        redacted=bool(row["redacted"]),
        created_at=row["created_at"],
        question=question,
    )


_EVENT_TYPES = tuple(_EVENT_TYPE_MAP)
_TYPE_PLACEHOLDERS = ",".join("?" * len(_EVENT_TYPES))


def get_recent_messages(
    db: sqlite3.Connection, project_id: int, limit: int = 5, hours: int = 24
) -> list[ChatMessage]:
    """Last `limit` messages, but only those within the last `hours` (whichever
    is smaller), oldest first -- the project page's "Recent Chat" widget."""
    rows = db.execute(
        f"""
        SELECT ae.*, s.agent_type FROM agent_events ae
        JOIN agent_sessions s ON s.id = ae.session_id
        WHERE s.project_id = ? AND s.role = 'GENERAL'
          AND ae.event_type IN ({_TYPE_PLACEHOLDERS})
          AND ae.created_at >= datetime('now', ?)
        ORDER BY ae.id DESC LIMIT ?
        """,
        (project_id, *_EVENT_TYPES, f"-{hours} hours", limit),
    ).fetchall()
    messages = [m for m in (_row_to_message(db, r) for r in rows) if m is not None]
    return list(reversed(messages))


def get_chat_history(
    db: sqlite3.Connection,
    project_id: int,
    limit: int | None = None,
    offset: int = 0,
    role: str | None = None,
    session_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[ChatMessage]:
    """Newest-first chat messages for a project, with optional filters."""
    where = [
        "s.project_id = ?", "s.role = 'GENERAL'", f"ae.event_type IN ({_TYPE_PLACEHOLDERS})",
    ]
    args: list[Any] = [project_id, *_EVENT_TYPES]
    if session_id is not None:
        where.append("ae.session_id = ?")
        args.append(session_id)
    if start_date is not None:
        where.append("ae.created_at >= ?")
        args.append(start_date)
    if end_date is not None:
        where.append("ae.created_at <= ?")
        args.append(end_date)
    sql = (
        "SELECT ae.*, s.agent_type FROM agent_events ae "
        "JOIN agent_sessions s ON s.id = ae.session_id "
        f"WHERE {' AND '.join(where)} ORDER BY ae.id DESC"
    )
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args.extend([limit, offset])
    rows = db.execute(sql, args).fetchall()
    messages = [m for m in (_row_to_message(db, r) for r in rows) if m is not None]
    if role is not None:
        messages = [m for m in messages if m.role == role]
    return messages


def get_messages_by_session(db: sqlite3.Connection, session_id: int) -> list[ChatMessage]:
    rows = db.execute(
        f"""
        SELECT ae.*, s.agent_type FROM agent_events ae
        JOIN agent_sessions s ON s.id = ae.session_id
        WHERE ae.session_id = ? AND s.role = 'GENERAL'
          AND ae.event_type IN ({_TYPE_PLACEHOLDERS})
        ORDER BY ae.id
        """,
        (session_id, *_EVENT_TYPES),
    ).fetchall()
    return [m for m in (_row_to_message(db, r) for r in rows) if m is not None]


def search_messages(db: sqlite3.Connection, project_id: int, query: str) -> list[ChatMessage]:
    """Case-insensitive substring search over chat content (no FTS table for
    agent_events; matches the approach `agent_models.search_sessions` already uses)."""
    query = query.strip()
    if not query:
        return []
    rows = db.execute(
        f"""
        SELECT ae.*, s.agent_type FROM agent_events ae
        JOIN agent_sessions s ON s.id = ae.session_id
        WHERE s.project_id = ? AND s.role = 'GENERAL'
          AND ae.event_type IN ({_TYPE_PLACEHOLDERS})
          AND instr(lower(ae.data), ?) > 0
        ORDER BY ae.id DESC
        """,
        (project_id, *_EVENT_TYPES, query.lower()),
    ).fetchall()
    return [m for m in (_row_to_message(db, r) for r in rows) if m is not None]


@dataclass
class SessionSummary:
    """An archived one-liner for a project/day window (task 62/67)."""

    id: int
    project_id: int
    period_start: str
    period_end: str
    summary: str
    message_count: int
    session_ids: list[int]
    created_at: str


def create_summary(
    db: sqlite3.Connection,
    project_id: int,
    period_start: str,
    period_end: str,
    summary: str,
    message_count: int,
    session_ids: list[int],
) -> int:
    cur = db.execute(
        "INSERT INTO session_summaries "
        "(project_id, period_start, period_end, summary, message_count, session_ids) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (project_id, period_start, period_end, summary, message_count, json.dumps(session_ids)),
    )
    db.commit()
    return cur.lastrowid


def list_summaries_for_project(db: sqlite3.Connection, project_id: int) -> list[SessionSummary]:
    rows = db.execute(
        "SELECT * FROM session_summaries WHERE project_id = ? ORDER BY period_end DESC",
        (project_id,),
    ).fetchall()
    return [_hydrate_summary(r) for r in rows]


def get_summary(db: sqlite3.Connection, summary_id: int) -> SessionSummary | None:
    row = db.execute("SELECT * FROM session_summaries WHERE id = ?", (summary_id,)).fetchone()
    return _hydrate_summary(row) if row else None


def _hydrate_summary(row: sqlite3.Row) -> SessionSummary:
    return SessionSummary(
        id=row["id"],
        project_id=row["project_id"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        summary=row["summary"],
        message_count=row["message_count"],
        session_ids=json.loads(row["session_ids"]),
        created_at=row["created_at"],
    )


def get_session_context(db: sqlite3.Connection, project_id: int, window_hours: int = 24) -> str:
    """Recent chat formatted as warm-start context for a new session's prompt
    (design doc §5.5); empty string if there is nothing recent to include."""
    messages = get_recent_messages(db, project_id, limit=50, hours=window_hours)
    text_messages = [m for m in messages if m.message_type in ("text", "ask")]
    if not text_messages:
        return ""
    lines = [f"[{m.created_at[11:16]} {m.role}: {m.content}]" for m in text_messages]
    return "[System Context]\nRecent conversation context for this project:\n\n" + "\n".join(lines)
