"""Chat history export (docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md §5.2.2, task 70)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.projects import models as project_models
from app.sessions import chat

_ROLE_LABELS = {"developer": "You", "agent": "Agent", "system": "System"}


def export_chat_history(
    db: sqlite3.Connection,
    project_id: int,
    format: str = "markdown",
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Render a project's chat history as `format` ('markdown' or 'json')."""
    if format not in ("markdown", "json"):
        raise ValueError(f"unsupported export format {format!r}")
    project = project_models.get_project(db, project_id)
    if project is None:
        raise ValueError(f"unknown project {project_id}")
    # Newest-first is right for the history page; a conversation thread reads
    # naturally oldest-first.
    messages = list(reversed(chat.get_chat_history(db, project_id, start_date=start_date, end_date=end_date)))
    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if format == "json":
        return json.dumps(
            {
                "project": project.name,
                "exported_at": exported_at,
                "message_count": len(messages),
                "messages": [
                    {
                        "session_id": m.session_id, "agent_type": m.agent_type, "role": m.role,
                        "message_type": m.message_type, "content": m.content,
                        "redacted": m.redacted, "created_at": m.created_at,
                    }
                    for m in messages
                ],
            },
            indent=2,
        )

    lines = [
        f"# Chat history - {project.name}", "", f"Exported {exported_at}", f"{len(messages)} messages", "",
    ]
    for m in messages:
        label = _ROLE_LABELS.get(m.role, m.role)
        marker = " [REDACTED]" if m.redacted else ""
        lines.append(f"**{m.created_at} {label}:**{marker} {m.content}")
        lines.append("")
    return "\n".join(lines)
