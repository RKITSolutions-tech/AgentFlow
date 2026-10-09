"""Session Topics (docs/SESSION_TOPICS.md): a named, project-scoped thread of
GENERAL sessions that carries a short rolling summary forward, so a new session
in the Topic starts from "where did we leave this" instead of project-wide
warm-start chatter or a full wiki dump.

Not to be confused with `knowledge_entries.topic` (the research wiki's taxonomy
tag, §3.2) -- that is why this lives in `discussion_topics`/`app/sessions/`.

The rolling summary reuses app/sessions/summarization.py's prompt template, line
cap and agent selection (`PLANNING_AGENT`); under `PLANNING_AGENT=fake`, or when
the agent fails, a deterministic line is used so archiving never depends on an
agent CLI being installed.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass

from app.agents import models as agent_models
from app.agents.models import AgentSession
from app.sessions import chat
from app.sessions.summarization import SUMMARY_LINE_MAX, SUMMARY_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

STATUSES = ("active", "archived")
NAME_MAX = 80
# Rolling summary cap (§4.3): past either limit the whole block is re-compressed.
SUMMARY_MAX_CHARS = 1500
SUMMARY_MAX_LINES = 20
# Last N text/ask messages of the Topic's most recent session put in the context.
CONTEXT_MESSAGES = 20
CONTEXT_MESSAGE_CHARS = 600
TRANSCRIPT_CHARS = 4000
AGENT_TIMEOUT_SECONDS = 180

RECOMPRESS_PROMPT_TEMPLATE = (
    "Condense the following running summary of a feature discussion into at most "
    "{max_lines} short commit-message-style lines (each under {line_max} chars), oldest "
    "first, keeping decisions, open questions and the current position:\n\n{summary}\n\n"
    "Condensed summary:"
)


class TopicError(ValueError):
    """User-facing problem with a Topic request (bad name, wrong project...)."""


@dataclass
class Topic:
    id: int
    project_id: int
    name: str
    status: str
    rolling_summary: str
    created_at: str
    updated_at: str
    session_count: int = 0
    last_activity_at: str = ""

    @property
    def summary_lines(self) -> list[str]:
        return [line for line in self.rolling_summary.splitlines() if line.strip()]

    def latest_lines(self, count: int = 2) -> list[str]:
        return self.summary_lines[-count:]

    @property
    def archived(self) -> bool:
        return self.status == "archived"


_SELECT = (
    "SELECT t.*, COUNT(s.id) AS session_count, "
    "MAX(COALESCE(MAX(s.last_activity_at), t.updated_at), t.updated_at) AS last_activity_at "
    "FROM discussion_topics t LEFT JOIN agent_sessions s ON s.topic_id = t.id"
)


def _hydrate(row: sqlite3.Row) -> Topic:
    return Topic(
        id=row["id"], project_id=row["project_id"], name=row["name"], status=row["status"],
        rolling_summary=row["rolling_summary"] or "", created_at=row["created_at"],
        updated_at=row["updated_at"], session_count=row["session_count"] or 0,
        last_activity_at=row["last_activity_at"] or row["updated_at"],
    )


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise TopicError("A topic name is required.")
    if len(name) > NAME_MAX:
        raise TopicError(f"Topic names are limited to {NAME_MAX} characters.")
    return name


def _name_taken(db: sqlite3.Connection, project_id: int, name: str, exclude_id: int | None = None) -> bool:
    row = db.execute(
        "SELECT id FROM discussion_topics WHERE project_id = ? AND lower(name) = lower(?) AND id != ?",
        (project_id, name, exclude_id or 0),
    ).fetchone()
    return row is not None


def create_topic(db: sqlite3.Connection, project_id: int, name: str) -> int:
    name = _clean_name(name)
    if db.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
        raise TopicError("That project doesn't exist.")
    if _name_taken(db, project_id, name):
        raise TopicError(f"This project already has a topic called {name!r}.")
    cur = db.execute("INSERT INTO discussion_topics (project_id, name) VALUES (?, ?)", (project_id, name))
    db.commit()
    return cur.lastrowid


def list_topics(db: sqlite3.Connection, project_id: int, include_archived: bool = False) -> list[Topic]:
    """A project's Topics, most recently active first; archived ones only on request."""
    where = "WHERE t.project_id = ?" + ("" if include_archived else " AND t.status = 'active'")
    rows = db.execute(
        f"{_SELECT} {where} GROUP BY t.id ORDER BY last_activity_at DESC, t.id DESC", (project_id,),
    ).fetchall()
    return [_hydrate(r) for r in rows]


def get_topic(db: sqlite3.Connection, topic_id: int) -> Topic | None:
    row = db.execute(f"{_SELECT} WHERE t.id = ? GROUP BY t.id", (topic_id,)).fetchone()
    return _hydrate(row) if row else None


def get_project_topic(db: sqlite3.Connection, project_id: int, topic_id: int) -> Topic:
    """The Topic, which must belong to `project_id` (raises TopicError otherwise)."""
    topic = get_topic(db, topic_id)
    if topic is None or topic.project_id != project_id:
        raise TopicError("Topic not found in this project.")
    return topic


def project_has_topics(db: sqlite3.Connection, project_id: int) -> bool:
    return db.execute("SELECT 1 FROM discussion_topics WHERE project_id = ? LIMIT 1", (project_id,)).fetchone() is not None


def rename_topic(db: sqlite3.Connection, topic_id: int, new_name: str) -> str:
    topic = get_topic(db, topic_id)
    if topic is None:
        raise TopicError("Topic not found.")
    name = _clean_name(new_name)
    if _name_taken(db, topic.project_id, name, exclude_id=topic_id):
        raise TopicError(f"This project already has a topic called {name!r}.")
    db.execute("UPDATE discussion_topics SET name = ? WHERE id = ?", (name, topic_id))
    db.commit()
    return name


def set_topic_status(db: sqlite3.Connection, topic_id: int, status: str) -> None:
    if status not in STATUSES:
        raise TopicError(f"Status must be one of {', '.join(STATUSES)}.")
    if db.execute("UPDATE discussion_topics SET status = ? WHERE id = ?", (status, topic_id)).rowcount == 0:
        raise TopicError("Topic not found.")
    db.commit()


def archive_topic(db: sqlite3.Connection, topic_id: int) -> None:
    """Hide the Topic from pickers; its sessions keep their topic_id."""
    set_topic_status(db, topic_id, "archived")


def restore_topic(db: sqlite3.Connection, topic_id: int) -> None:
    set_topic_status(db, topic_id, "active")


def resolve_topic_choice(db: sqlite3.Connection, project_id: int, choice: str, new_name: str = "") -> int | None:
    """The start form's / "Add to Topic" picker's value: "" (no topic), "new"
    (create `new_name`) or an existing Topic id in this project."""
    choice = (choice or "").strip()
    if not choice:
        return None
    if choice == "new":
        return create_topic(db, project_id, new_name)
    if not choice.isdigit():
        raise TopicError("Choose a topic from the list.")
    topic = get_project_topic(db, project_id, int(choice))
    if topic.archived:
        raise TopicError(f"Topic {topic.name!r} is archived; restore it first.")
    return topic.id


def topic_sessions(db: sqlite3.Connection, topic_id: int) -> list[AgentSession]:
    """Every session in the Topic -- archived ones included (§5.3) -- newest first."""
    rows = db.execute(
        "SELECT * FROM agent_sessions WHERE topic_id = ? ORDER BY last_activity_at DESC, id DESC", (topic_id,),
    ).fetchall()
    return [agent_models._hydrate_session(r) for r in rows]


def topic_names(db: sqlite3.Connection, topic_ids) -> dict[int, str]:
    """{topic id: name} for badge rendering on session lists."""
    ids = sorted({t for t in topic_ids if t})
    if not ids:
        return {}
    rows = db.execute(
        f"SELECT id, name FROM discussion_topics WHERE id IN ({','.join('?' * len(ids))})", ids,
    ).fetchall()
    return {r["id"]: r["name"] for r in rows}


def get_topic_context(db: sqlite3.Connection, topic_id: int, exclude_session_id: int | None = None) -> str:
    """Starting-prompt context for a session in the Topic (§5.1): the rolling
    summary plus the last text/ask messages of the Topic's most recent session
    that has any. No 24h cutoff -- a Topic can go quiet for weeks and still be
    current. Empty string when there is nothing to carry forward yet."""
    topic = get_topic(db, topic_id)
    if topic is None:
        return ""
    recent: list[chat.ChatMessage] = []
    recent_session = None
    for session in topic_sessions(db, topic_id):
        if session.id == exclude_session_id:
            continue
        messages = [m for m in chat.get_messages_by_session(db, session.id) if m.message_type in ("text", "ask")]
        if messages:
            recent, recent_session = messages[-CONTEXT_MESSAGES:], session
            break
    if not topic.summary_lines and not recent:
        return ""
    parts = [f"[System Context]\nThis session continues the topic \"{topic.name}\" in this project."]
    if topic.summary_lines:
        parts.append("Summary of earlier sessions in this topic (oldest first):\n" + "\n".join(
            f"- {line.lstrip('- ').strip()}" for line in topic.summary_lines
        ))
    if recent:
        lines = []
        for m in recent:
            content = m.content if len(m.content) <= CONTEXT_MESSAGE_CHARS else m.content[:CONTEXT_MESSAGE_CHARS] + " [...]"
            lines.append(f"[{m.created_at[:16]} {m.role}: {content}]")
        parts.append(f"Most recent messages (session {recent_session.id}):\n" + "\n".join(lines))
    return "\n\n".join(parts)


# -- rolling summary (§4.3) ------------------------------------------------------------


def _planning_kind(app_config) -> str:
    return str(app_config.get("PLANNING_AGENT", "codex")).lower()


def _ask_agent(db: sqlite3.Connection, app_config, project_id: int, prompt: str) -> str:
    """One-shot PLANNING-role agent turn; returns its reply text ('' on any
    failure). Uses the same adapter selection and execution context as sprint
    planning, and waits for the turn to finish (bounded) before reading it."""
    from app.pipelines.manager import default_agent_factory
    from app.projects import models as project_models
    from app.sprints import planning_agent

    project = project_models.get_project(db, project_id)
    repo = next((r for r in project.repositories if r.is_primary), None) or (
        project.repositories[0] if project.repositories else None
    )
    if repo is None:
        return ""
    try:
        provider = planning_agent._host_provider(app_config)
        context = planning_agent.build_context(app_config, project_id, repo.path, db=db)
        adapter = default_agent_factory(app_config, provider)(db)
        session = adapter.start(context, prompt, options={"role": "PLANNING"})
        deadline = time.monotonic() + AGENT_TIMEOUT_SECONDS
        while session.status in agent_models.ACTIVE_STATUSES and time.monotonic() < deadline:
            time.sleep(1)
            session = adapter.status(session.id)
        return "\n".join(e.data for e in adapter.stream(session.id) if e.event_type == "AgentText")
    except Exception:  # noqa: BLE001 -- archiving must not fail on the summarizer
        logger.exception("topic summary agent failed for project %s", project_id)
        return ""


def _first_line(reply: str) -> str:
    return next((line.strip() for line in reply.splitlines() if line.strip()), "")


def _fallback_line(db: sqlite3.Connection, session: AgentSession, message_count: int) -> str:
    title = agent_models.session_title(db, session) or f"session {session.id}"
    return f"{title} ({message_count} messages)"[:SUMMARY_LINE_MAX]


def _over_cap(summary: str) -> bool:
    lines = [line for line in summary.splitlines() if line.strip()]
    return len(summary) > SUMMARY_MAX_CHARS or len(lines) > SUMMARY_MAX_LINES


def _trim_to_cap(lines: list[str]) -> list[str]:
    """Deterministic re-compression: keep the newest lines that fit, noting how
    many older ones were folded away."""
    kept: list[str] = []
    for line in reversed(lines):
        candidate = [line, *kept]
        if len(candidate) > SUMMARY_MAX_LINES - 1 or len("\n".join(candidate)) > SUMMARY_MAX_CHARS - 60:
            break
        kept = candidate
    dropped = len(lines) - len(kept)
    return ([f"({dropped} earlier session summaries condensed away)"] if dropped else []) + kept


def recompress_summary(db: sqlite3.Connection, app_config, project_id: int, summary: str) -> str:
    """Shrink an over-cap rolling summary back under the cap, via the
    summarization agent when one is configured, else by trimming."""
    lines = [line.strip() for line in summary.splitlines() if line.strip()]
    if _planning_kind(app_config) != "fake":
        reply = _ask_agent(db, app_config, project_id, RECOMPRESS_PROMPT_TEMPLATE.format(
            max_lines=SUMMARY_MAX_LINES // 2, line_max=SUMMARY_LINE_MAX, summary="\n".join(lines),
        ))
        condensed = [line.strip().lstrip("-* ").strip()[:SUMMARY_LINE_MAX] for line in reply.splitlines() if line.strip()]
        if condensed and not _over_cap("\n".join(condensed)):
            return "\n".join(condensed)
    return "\n".join(_trim_to_cap(lines))


def append_session_summary_to_topic(db: sqlite3.Connection, app_config, session_id: int) -> str | None:
    """Fold a Topic session's not-yet-summarized messages into its Topic's
    rolling summary (one line, newest last) and re-compress past the cap.

    Only reads `agent_events` rows, which `add_agent_event` has already
    redacted (§8). Idempotent per message: the last summarized event id is kept
    on the session, so archiving, restoring and re-archiving only adds a line
    for what happened in between. Returns the appended line, or None."""
    session = agent_models.get_agent_session(db, session_id)
    if session is None or session.topic_id is None:
        return None
    topic = get_topic(db, session.topic_id)
    if topic is None:
        return None
    through = int(session.metadata.get("topic_summarized_through") or 0)
    messages = [
        m for m in chat.get_messages_by_session(db, session_id)
        if m.message_type in ("text", "ask") and m.event_id > through
    ]
    if not messages:
        return None

    line = ""
    if _planning_kind(app_config) != "fake":
        transcript = "\n".join(f"{m.role}: {m.content}" for m in messages)[-TRANSCRIPT_CHARS:]
        line = _first_line(_ask_agent(db, app_config, session.project_id, SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript)))
    line = (line or _fallback_line(db, session, len(messages)))[:SUMMARY_LINE_MAX]

    # Re-read: another session in the Topic may have appended meanwhile.
    current = (get_topic(db, topic.id) or topic).rolling_summary.rstrip("\n")
    summary = f"{current}\n{line}" if current else line
    if _over_cap(summary):
        summary = recompress_summary(db, app_config, session.project_id, summary)
    db.execute(
        "UPDATE discussion_topics SET rolling_summary = ?, updated_at = datetime('now') WHERE id = ?",
        (summary, topic.id),
    )
    db.commit()
    agent_models.update_session_metadata(db, session_id, topic_summarized_through=messages[-1].event_id)
    return line


def summarize_on_archive(app_config, db: sqlite3.Connection, session_id: int) -> None:
    """Called when a session is archived. With the fake agent the summary is
    deterministic and written inline; a real agent turn can take minutes, so it
    runs on a background thread with its own connection and the archive
    request returns at once."""
    session = agent_models.get_agent_session(db, session_id)
    if session is None or session.topic_id is None:
        return
    if _planning_kind(app_config) == "fake":
        _safe_append(db, app_config, session_id)
        return
    config = dict(app_config)

    def work():
        conn = sqlite3.connect(config["DATABASE_PATH"], timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            _safe_append(conn, config, session_id)
        finally:
            conn.close()

    threading.Thread(target=work, daemon=True, name=f"topic-summary-{session_id}").start()


def _safe_append(db: sqlite3.Connection, app_config, session_id: int) -> None:
    try:
        append_session_summary_to_topic(db, app_config, session_id)
    except Exception:  # noqa: BLE001 -- the archive itself already succeeded
        logger.exception("could not add session %s to its topic summary", session_id)
