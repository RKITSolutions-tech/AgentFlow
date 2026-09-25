"""Application shell: sidebar data and small template helpers.

Every page renders inside the persistent shell (sidebar + main pane, see
docs/CLOUDCLI_UX_REFERENCE.md). This module supplies the sidebar's project and
session list, works out which project/session the current request belongs to,
and registers the presentation helpers the shell templates use.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from flask import Flask, request

from app.db import get_db
from app.projects import models as project_models

# Recent sessions shown under each project in the sidebar. The full list is
# always one click away on the project's Sessions tab.
SIDEBAR_SESSIONS_PER_PROJECT = 12

# Session status -> coarse UI state used for the sidebar indicator.
_RUNNING = {"STARTING", "RUNNING"}
_FAILED = {"FAILED"}


def relative_age(value: str | None) -> str:
    """Render a SQLite UTC timestamp as a short age such as `5m` or `3d`."""
    if not value:
        return ""
    try:
        moment = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    seconds = int((datetime.now(timezone.utc) - moment).total_seconds())
    if seconds < 60:
        return "now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def session_state(status: str) -> str:
    """Map a raw session status to `running`, `failed` or `idle`."""
    if status in _RUNNING:
        return "running"
    if status in _FAILED:
        return "failed"
    return "idle"


def session_title(agent_type: str, session_id: int) -> str:
    """Sessions have no user-visible title yet, so derive a stable label."""
    return f"{agent_type.capitalize()} #{session_id}"


def _sidebar_sessions(db: sqlite3.Connection) -> dict[int, dict]:
    """Recent sessions and total counts per project, in two cheap queries."""
    counts = {
        row["project_id"]: row["n"]
        for row in db.execute(
            "SELECT project_id, COUNT(*) AS n FROM agent_sessions GROUP BY project_id"
        )
    }
    grouped: dict[int, dict] = {pid: {"total": n, "recent": []} for pid, n in counts.items()}
    rows = db.execute(
        "SELECT id, project_id, agent_type, status, started_at, last_activity_at "
        "FROM agent_sessions ORDER BY COALESCE(last_activity_at, started_at) DESC, id DESC"
    )
    for row in rows:
        bucket = grouped[row["project_id"]]
        if len(bucket["recent"]) >= SIDEBAR_SESSIONS_PER_PROJECT:
            continue
        bucket["recent"].append(
            {
                "id": row["id"],
                "title": session_title(row["agent_type"], row["id"]),
                "state": session_state(row["status"]),
                "age": relative_age(row["last_activity_at"] or row["started_at"]),
            }
        )
    return grouped


def _active_ids(db: sqlite3.Connection) -> tuple[int | None, int | None]:
    """Return (project_id, session_id) for the current request, if any."""
    view_args = request.view_args or {}
    session_id = view_args.get("session_id")
    project_id = view_args.get("project_id")
    if session_id is not None and project_id is None:
        row = db.execute(
            "SELECT project_id FROM agent_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        project_id = row["project_id"] if row else None
    return project_id, session_id


def _shell_context() -> dict:
    db = get_db()
    projects = project_models.list_projects(db)
    sessions = _sidebar_sessions(db)
    active_project_id, active_session_id = _active_ids(db)
    return {
        "shell": {
            "projects": [
                {
                    "id": project.id,
                    "name": project.name,
                    "session_total": sessions.get(project.id, {}).get("total", 0),
                    "sessions": sessions.get(project.id, {}).get("recent", []),
                }
                for project in projects
            ],
            "active_project_id": active_project_id,
            "active_session_id": active_session_id,
            "endpoint": request.endpoint or "",
        }
    }


def init_shell(app: Flask) -> None:
    app.context_processor(_shell_context)
    app.add_template_filter(relative_age, "relative_age")
