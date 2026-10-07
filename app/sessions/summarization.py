"""Nightly chat archival (docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md §5.3, task 67).

Groups GENERAL-session chat messages 24h-30 days old by project and day, asks an
agent to compress each day into a one-line commit-message-style summary, and
records it via `app.sessions.chat.create_summary`. Nothing is deleted from
`agent_events` -- resume, search, fork and the session transcript page all still
need those rows; the "archive" is additive.

Adapter selection reuses `app.pipelines.manager.default_agent_factory` keyed off
the shared `PLANNING_AGENT` config, the same ad-hoc-agent-session convention
`app/backlog/research.py` and `app/sprints/planning_agent.py` already use --
there is no separate `SUMMARIZATION_AGENT` setting.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from app.agents.base import AgentContext
from app.execution.host import HostExecutionProvider
from app.pipelines.manager import default_agent_factory
from app.projects import models as project_models
from app.sessions import chat

logger = logging.getLogger(__name__)

SUMMARY_PROMPT_TEMPLATE = (
    "Summarize the following developer-agent conversation into one "
    "commit-message-style line (under 72 chars):\n\n{transcript}\n\nSummary:"
)
SUMMARY_LINE_MAX = 72
_PLANNING_ROLE = "PLANNING"  # app/sprints/planning_agent.PLANNING_ROLE; not imported to avoid a cycle


def _eligible_days(db: sqlite3.Connection, project_id: int, now: datetime) -> dict[str, list]:
    """Chat text/ask messages 24h-30 days old, grouped by calendar day."""
    window_start = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    window_end = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    messages = chat.get_chat_history(db, project_id, start_date=window_start, end_date=window_end)
    by_day: dict[str, list] = {}
    for m in messages:
        if m.message_type not in ("text", "ask"):
            continue
        by_day.setdefault(m.created_at[:10], []).append(m)
    return by_day


def _fallback_summary(message_count: int) -> str:
    return f"session: {message_count} messages exchanged"[:SUMMARY_LINE_MAX]


def _summarize_day(db, app_config, provider, project_id: int, messages: list) -> str:
    kind = str(app_config.get("PLANNING_AGENT", "codex")).lower()
    if kind == "fake":
        # Deterministic, like app/backlog/research.py's _fake_report: no CLI needed.
        return _fallback_summary(len(messages))

    project = project_models.get_project(db, project_id)
    repo = next((r for r in project.repositories if r.is_primary), None) or (
        project.repositories[0] if project.repositories else None
    )
    working_directory = repo.path if repo else app_config["ALLOWED_PROJECT_ROOTS"][0]
    context = AgentContext(project_id=project_id, working_directory=working_directory)

    transcript = "\n".join(f"{m.role}: {m.content}" for m in messages)[:4000]
    prompt = SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript)
    adapter = default_agent_factory(app_config, provider)(db)
    try:
        session = adapter.start(context, prompt, options={"role": _PLANNING_ROLE})
        session = adapter.status(session.id)
        reply = "\n".join(e.data for e in adapter.stream(session.id) if e.event_type == "AgentText")
    except Exception:
        logger.exception("summarization agent session failed for project %s", project_id)
        return _fallback_summary(len(messages))
    line = next((l.strip() for l in reply.splitlines() if l.strip()), "")
    return line[:SUMMARY_LINE_MAX] if line else _fallback_summary(len(messages))


def summarize_project(db: sqlite3.Connection, app_config, project_id: int, provider, now: datetime | None = None) -> list[int]:
    """Summarize `project_id`'s not-yet-archived chat from 24h-30 days ago.

    Idempotent: a day already covered by an existing `session_summaries` row
    (matched on its date) is skipped, so re-running the job never double-counts.
    Returns the ids of any new summaries.
    """
    now = now or datetime.now(timezone.utc)
    by_day = _eligible_days(db, project_id, now)
    if not by_day:
        return []
    already_summarized = {s.period_start[:10] for s in chat.list_summaries_for_project(db, project_id)}

    new_ids = []
    for day in sorted(by_day):
        if day in already_summarized:
            continue
        day_messages = sorted(by_day[day], key=lambda m: m.created_at)
        summary_text = _summarize_day(db, app_config, provider, project_id, day_messages)
        session_ids = sorted({m.session_id for m in day_messages})
        summary_id = chat.create_summary(
            db, project_id,
            period_start=f"{day} 00:00:00", period_end=f"{day} 23:59:59",
            summary=summary_text, message_count=len(day_messages), session_ids=session_ids,
        )
        new_ids.append(summary_id)
    return new_ids


def run_for_all_projects(app_config, db_path: str | None = None) -> dict[int, list[int]]:
    """Entry point for the nightly job: summarize every project's eligible chat."""
    db_path = db_path or app_config["DATABASE_PATH"]
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        provider = HostExecutionProvider(db_path, app_config["ALLOWED_PROJECT_ROOTS"])
        project_ids = [row["id"] for row in conn.execute("SELECT id FROM projects").fetchall()]
        return {pid: summarize_project(conn, app_config, pid, provider) for pid in project_ids}
    finally:
        conn.close()


class SessionSummarizer:
    """Registers the nightly job on `app.extensions` (mirrors `project_lock.Sweeper`'s
    plain-thread pattern, using APScheduler's BackgroundScheduler for the cron
    trigger instead of a sleep loop)."""

    def __init__(self, config, hour: int | None = None):
        self._config = config
        self._hour = hour if hour is not None else int(os.environ.get("AGENTFLOW_SUMMARIZATION_HOUR", "3"))
        self._scheduler = None

    def start(self) -> "SessionSummarizer":
        from apscheduler.schedulers.background import BackgroundScheduler

        self._scheduler = BackgroundScheduler(daemon=True)
        self._scheduler.add_job(self._run, "cron", hour=self._hour, id="session_summarization")
        self._scheduler.start()
        return self

    def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)

    def _run(self) -> None:
        try:
            run_for_all_projects(self._config)
        except Exception:
            logger.exception("nightly session summarization failed")
