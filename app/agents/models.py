from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class AgentSession:
    id: int
    project_id: int
    agent_type: str
    role: str
    external_session_id: str | None
    execution_provider: str
    execution_target: str
    status: str
    metadata: dict[str, Any]
    started_at: str
    last_activity_at: str


@dataclass
class AgentEvent:
    id: int
    session_id: int
    event_type: str
    data: str
    created_at: str


@dataclass
class AgentResult:
    id: int
    session_id: int
    status: str
    git_diff: str
    test_status: str | None
    test_exit_code: int | None
    test_output: str
    created_at: str


QUESTION_PENDING = "PENDING"
QUESTION_ANSWERED = "ANSWERED"
QUESTION_SKIPPED = "SKIPPED"

# "Other" is part of the question UI contract, not something the agent
# supplies (docs/AGENT_ADAPTER.md section 12), so it is added at display time
# and never stored with the agent's own options.
OTHER_OPTION_LABEL = "Other..."


@dataclass
class ClarifyingQuestion:
    """A structured multiple-choice question an agent raised mid-session.

    Persisted separately from plain messages so the option list survives
    end-to-end. A ``ClarifyingQuestion`` AgentEvent points at the row.
    """

    id: int
    session_id: int
    event_id: int | None
    external_id: str | None
    header: str
    question: str
    multi_select: bool
    options: list[dict[str, str]]
    status: str
    answer: dict[str, Any] | None
    created_at: str
    answered_at: str | None

    def display_options(self) -> list[dict[str, str]]:
        """Agent options followed by the always-present free-text "Other"."""
        return [*self.options, {"label": OTHER_OPTION_LABEL, "description": "", "other": "1"}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "header": self.header,
            "question": self.question,
            "multi_select": self.multi_select,
            "options": self.display_options(),
            "status": self.status,
            "answer": self.answer,
        }

    def answer_text(self) -> str:
        """Plain-text reply to hand back to an agent that has no structured channel."""
        parts = [*(self.answer or {}).get("selected", [])]
        other = (self.answer or {}).get("other", "")
        if other:
            parts.append(other)
        return f"Answer to \"{self.question}\": {', '.join(parts)}"


def create_agent_session(
    db: sqlite3.Connection,
    project_id: int,
    agent_type: str,
    role: str = "GENERAL",
    execution_provider: str = "host",
    execution_target: str = "",
    metadata: dict[str, Any] | None = None,
) -> int:
    cur = db.execute(
        "INSERT INTO agent_sessions "
        "(project_id, agent_type, role, execution_provider, execution_target, status, metadata) "
        "VALUES (?, ?, ?, ?, ?, 'STARTING', ?)",
        (
            project_id,
            agent_type,
            role,
            execution_provider,
            execution_target,
            json.dumps(metadata or {}),
        ),
    )
    db.commit()
    return cur.lastrowid


def set_session_status(db: sqlite3.Connection, session_id: int, status: str) -> None:
    db.execute(
        "UPDATE agent_sessions SET status = ?, last_activity_at = datetime('now') WHERE id = ?",
        (status, session_id),
    )
    db.commit()


def set_session_metadata(
    db: sqlite3.Connection, session_id: int, metadata: dict[str, Any]
) -> None:
    db.execute(
        "UPDATE agent_sessions SET metadata = ?, last_activity_at = datetime('now') WHERE id = ?",
        (json.dumps(metadata), session_id),
    )
    db.commit()


def set_external_session_id(
    db: sqlite3.Connection, session_id: int, external_session_id: str
) -> None:
    db.execute(
        "UPDATE agent_sessions SET external_session_id = ? WHERE id = ?",
        (external_session_id, session_id),
    )
    db.commit()


def get_agent_session(db: sqlite3.Connection, session_id: int) -> AgentSession | None:
    row = db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    return _hydrate_session(row)


def list_agent_sessions_for_project(
    db: sqlite3.Connection, project_id: int
) -> list[AgentSession]:
    rows = db.execute(
        "SELECT * FROM agent_sessions WHERE project_id = ? ORDER BY id", (project_id,)
    ).fetchall()
    return [_hydrate_session(r) for r in rows]


def update_session_metadata(
    db: sqlite3.Connection, session_id: int, **changes: Any
) -> None:
    """Merge ``changes`` into the session metadata (``None`` removes a key).

    Unlike ``set_session_metadata`` this re-reads the row first, so it does not
    clobber keys an adapter wrote in the meantime. It leaves
    ``last_activity_at`` alone: renaming or archiving is not agent activity.
    """
    row = db.execute("SELECT metadata FROM agent_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown agent session {session_id}")
    metadata = json.loads(row["metadata"])
    for key, value in changes.items():
        if value is None:
            metadata.pop(key, None)
        else:
            metadata[key] = value
    db.execute(
        "UPDATE agent_sessions SET metadata = ? WHERE id = ?", (json.dumps(metadata), session_id)
    )
    db.commit()


SESSION_TITLE_MAX = 80
ACTIVE_STATUSES = ("STARTING", "RUNNING")
# Adapter-internal turn bookkeeping that must not leak into a forked session.
_FORK_SKIPPED_METADATA = (
    "process_id",
    "process_event_cursor",
    "turn_finalized",
    "last_stderr_line",
    "last_agent_message",
    "cursor",
    "archived_at",
    "usage",
)


def derive_title(text: str) -> str:
    """First non-empty line of ``text``, trimmed to a sensible title length."""
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            if len(line) > SESSION_TITLE_MAX:
                line = line[: SESSION_TITLE_MAX - 1].rstrip() + "\u2026"
            return line
    return ""


def session_title(db: sqlite3.Connection, session: AgentSession) -> str:
    """Stored title, else one derived from the first real prompt, else empty."""
    title = session.metadata.get("title")
    if title:
        return title
    return _first_prompt_title(db, session.id)


def _first_prompt_title(db: sqlite3.Connection, session_id: int) -> str:
    rows = db.execute(
        "SELECT data FROM agent_events WHERE session_id = ? AND event_type = 'PromptSubmitted' "
        "ORDER BY id",
        (session_id,),
    ).fetchall()
    for row in rows:
        if row["data"].strip() and row["data"].strip() != PLACEHOLDER_PROMPT:
            return derive_title(row["data"])
    return ""


# The sessions UI starts a session with this stand-in prompt; it says nothing
# about the work, so it is never used as a title.
PLACEHOLDER_PROMPT = "Starting session..."


def auto_title_session(db: sqlite3.Connection, session_id: int, prompt: str) -> None:
    """Title an untitled session from its first real prompt."""
    session = get_agent_session(db, session_id)
    if session is None or session.metadata.get("title"):
        return
    if prompt.strip() == PLACEHOLDER_PROMPT:
        return
    title = derive_title(prompt)
    if title:
        update_session_metadata(db, session_id, title=title)


def rename_session(db: sqlite3.Connection, session_id: int, title: str) -> str:
    """Set a title; an empty one clears it so the auto-title applies again."""
    title = derive_title(title)
    update_session_metadata(db, session_id, title=title or None)
    return title


def is_archived(session: AgentSession) -> bool:
    return bool(session.metadata.get("archived_at"))


def set_session_archived(db: sqlite3.Connection, session_id: int, archived: bool) -> None:
    session = get_agent_session(db, session_id)
    if session is None:
        raise ValueError(f"Unknown agent session {session_id}")
    if archived and session.status in ACTIVE_STATUSES:
        raise ValueError("Stop the session before archiving it")
    stamp = db.execute("SELECT datetime('now') AS now").fetchone()["now"] if archived else None
    update_session_metadata(db, session_id, archived_at=stamp)


def fork_session(db: sqlite3.Connection, session_id: int) -> int:
    """Copy a session's history into a new, independent session.

    The fork gets its own metadata (adapter turn state and the archived flag
    are dropped) and no external agent session id, so the agent itself starts
    fresh: only adapters that can continue from copied history should offer it
    (capability ``fork``).
    """
    source = get_agent_session(db, session_id)
    if source is None:
        raise ValueError(f"Unknown agent session {session_id}")
    metadata = {k: v for k, v in source.metadata.items() if k not in _FORK_SKIPPED_METADATA}
    metadata["forked_from"] = source.id
    base = session_title(db, source)
    metadata["title"] = f"Fork of {base}" if base else f"Fork of session {source.id}"
    new_id = create_agent_session(
        db,
        source.project_id,
        source.agent_type,
        role=source.role,
        execution_provider=source.execution_provider,
        execution_target=source.execution_target,
        metadata=metadata,
    )
    db.execute(
        "INSERT INTO agent_events (session_id, event_type, data, created_at) "
        "SELECT ?, event_type, data, created_at FROM agent_events "
        "WHERE session_id = ? AND event_type != 'ClarifyingQuestion' ORDER BY id",
        (new_id, session_id),
    )
    db.commit()
    set_session_status(db, new_id, "COMPLETED")
    return new_id


def session_usage(session: AgentSession) -> dict[str, int]:
    """Cumulative token usage recorded by the adapter (empty if none)."""
    usage = session.metadata.get("usage") or {}
    return {k: int(v) for k, v in usage.items() if isinstance(v, (int, float))}


def search_sessions(
    db: sqlite3.Connection,
    query: str,
    project_id: int | None = None,
    include_archived: bool = False,
    limit: int = 50,
) -> list[tuple[AgentSession, str]]:
    """Find sessions whose title or message text contains ``query``.

    Returns ``(session, snippet)`` pairs, most recently active first. The
    snippet is the matching text, or an empty string for a title-only match.
    """
    query = query.strip()
    if not query:
        return []
    needle = query.lower()
    where = "WHERE project_id = ?" if project_id is not None else ""
    args = (project_id,) if project_id is not None else ()
    rows = db.execute(
        f"SELECT * FROM agent_sessions {where} ORDER BY last_activity_at DESC, id DESC", args
    ).fetchall()
    results = []
    for row in rows:
        session = _hydrate_session(row)
        if is_archived(session) and not include_archived:
            continue
        title = session_title(db, session)
        if needle in title.lower():
            results.append((session, ""))
        else:
            event = db.execute(
                "SELECT data FROM agent_events WHERE session_id = ? "
                "AND event_type IN ('PromptSubmitted', 'AgentText', 'AgentComplete') "
                "AND instr(lower(data), ?) > 0 ORDER BY id LIMIT 1",
                (session.id, needle),
            ).fetchone()
            if event is None:
                continue
            results.append((session, _snippet(event["data"], needle)))
        if len(results) >= limit:
            break
    return results


def _snippet(text: str, needle: str, radius: int = 60) -> str:
    text = " ".join(text.split())
    at = text.lower().find(needle)
    start = max(0, at - radius)
    end = min(len(text), at + len(needle) + radius)
    return ("\u2026" if start else "") + text[start:end] + ("\u2026" if end < len(text) else "")


def list_sessions(
    db: sqlite3.Connection,
    project_id: int | None = None,
    archived: bool = False,
    running_only: bool = False,
    limit: int | None = None,
) -> list[AgentSession]:
    """Sessions across projects (or one), most recently active first."""
    where = "WHERE project_id = ?" if project_id is not None else ""
    args = (project_id,) if project_id is not None else ()
    rows = db.execute(
        f"SELECT * FROM agent_sessions {where} ORDER BY last_activity_at DESC, id DESC", args
    ).fetchall()
    sessions = [_hydrate_session(r) for r in rows]
    sessions = [s for s in sessions if is_archived(s) == archived]
    if running_only:
        sessions = [s for s in sessions if s.status in ACTIVE_STATUSES]
    return sessions[:limit] if limit else sessions


def delete_agent_session(db: sqlite3.Connection, session_id: int) -> None:
    db.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))
    db.commit()


def _hydrate_session(row: sqlite3.Row) -> AgentSession:
    return AgentSession(
        id=row["id"],
        project_id=row["project_id"],
        agent_type=row["agent_type"],
        role=row["role"],
        external_session_id=row["external_session_id"],
        execution_provider=row["execution_provider"],
        execution_target=row["execution_target"],
        status=row["status"],
        metadata=json.loads(row["metadata"]),
        started_at=row["started_at"],
        last_activity_at=row["last_activity_at"],
    )


def add_agent_event(
    db: sqlite3.Connection, session_id: int, event_type: str, data: str = ""
) -> int:
    cur = db.execute(
        "INSERT INTO agent_events (session_id, event_type, data) VALUES (?, ?, ?)",
        (session_id, event_type, data),
    )
    db.commit()
    return cur.lastrowid


def list_agent_events(
    db: sqlite3.Connection, session_id: int, after_id: int | None = None
) -> list[AgentEvent]:
    if after_id is None:
        rows = db.execute(
            "SELECT * FROM agent_events WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM agent_events WHERE session_id = ? AND id > ? ORDER BY id",
            (session_id, after_id),
        ).fetchall()
    return [_hydrate_event(r) for r in rows]


def _hydrate_event(row: sqlite3.Row) -> AgentEvent:
    return AgentEvent(
        id=row["id"],
        session_id=row["session_id"],
        event_type=row["event_type"],
        data=row["data"],
        created_at=row["created_at"],
    )


def record_result(
    db: sqlite3.Connection,
    session_id: int,
    status: str,
    git_diff: str = "",
    test_status: str | None = None,
    test_exit_code: int | None = None,
    test_output: str = "",
) -> int:
    cur = db.execute(
        "INSERT INTO agent_results "
        "(session_id, status, git_diff, test_status, test_exit_code, test_output) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, status, git_diff, test_status, test_exit_code, test_output),
    )
    db.commit()
    return cur.lastrowid


def get_result(db: sqlite3.Connection, session_id: int) -> AgentResult | None:
    row = db.execute(
        "SELECT * FROM agent_results WHERE session_id = ? ORDER BY id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return AgentResult(
        id=row["id"],
        session_id=row["session_id"],
        status=row["status"],
        git_diff=row["git_diff"],
        test_status=row["test_status"],
        test_exit_code=row["test_exit_code"],
        test_output=row["test_output"],
        created_at=row["created_at"],
    )


def normalize_options(options: list[Any]) -> list[dict[str, str]]:
    normalized = []
    for option in options:
        if isinstance(option, str):
            option = {"label": option}
        label = str(option.get("label", "")).strip()
        if not label:
            raise ValueError("question option needs a non-empty label")
        normalized.append({"label": label, "description": str(option.get("description", ""))})
    if not normalized:
        raise ValueError("a clarifying question needs at least one option")
    return normalized


def create_clarifying_question(
    db: sqlite3.Connection,
    session_id: int,
    question: str,
    options: list[Any],
    header: str = "",
    multi_select: bool = False,
    external_id: str | None = None,
) -> ClarifyingQuestion:
    """Persist a question and emit its ``ClarifyingQuestion`` event."""
    if not question.strip():
        raise ValueError("question text is required")
    normalized = normalize_options(options)
    cur = db.execute(
        "INSERT INTO agent_questions "
        "(session_id, external_id, header, question, multi_select, options) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, external_id, header, question, int(multi_select), json.dumps(normalized)),
    )
    question_id = cur.lastrowid
    event_id = add_agent_event(
        db, session_id, "ClarifyingQuestion", json.dumps({"question_id": question_id})
    )
    db.execute("UPDATE agent_questions SET event_id = ? WHERE id = ?", (event_id, question_id))
    db.commit()

    from app.notifications import models as notification_models

    notification_models.notify(
        db,
        session_id,
        "blocked_on_question",
        f"{header or 'Agent'} needs your input: {question}",
    )
    return get_clarifying_question(db, question_id)


def get_clarifying_question(
    db: sqlite3.Connection, question_id: int
) -> ClarifyingQuestion | None:
    row = db.execute("SELECT * FROM agent_questions WHERE id = ?", (question_id,)).fetchone()
    return _hydrate_question(row) if row else None


def list_clarifying_questions(
    db: sqlite3.Connection, session_id: int, status: str | None = None
) -> list[ClarifyingQuestion]:
    sql = "SELECT * FROM agent_questions WHERE session_id = ?"
    params: list[Any] = [session_id]
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    rows = db.execute(sql + " ORDER BY id", params).fetchall()
    return [_hydrate_question(r) for r in rows]


def answer_clarifying_question(
    db: sqlite3.Connection,
    question_id: int,
    selected: list[str] | None = None,
    other_text: str = "",
) -> ClarifyingQuestion:
    """Record an answer. Raises ValueError if unknown, closed, or empty."""
    q = _require_pending(db, question_id)
    selected = selected or []
    other_text = other_text.strip()
    known = {o["label"] for o in q.options}
    if not selected and not other_text:
        raise ValueError("an answer needs a selected option or other text")
    if any(s not in known for s in selected):
        raise ValueError("selected option is not one of the question's options")
    if len(selected) > 1 and not q.multi_select:
        raise ValueError("question allows a single selection")
    _close_question(db, question_id, QUESTION_ANSWERED, {"selected": selected, "other": other_text})
    return get_clarifying_question(db, question_id)


def skip_clarifying_question(db: sqlite3.Connection, question_id: int) -> ClarifyingQuestion:
    """Record a skip: a distinct outcome from an answer (agent must wait)."""
    _require_pending(db, question_id)
    _close_question(db, question_id, QUESTION_SKIPPED, None)
    return get_clarifying_question(db, question_id)


def _require_pending(db: sqlite3.Connection, question_id: int) -> ClarifyingQuestion:
    q = get_clarifying_question(db, question_id)
    if q is None:
        raise ValueError(f"unknown question {question_id}")
    if q.status != QUESTION_PENDING:
        raise ValueError(f"question {question_id} is already {q.status.lower()}")
    return q


def _close_question(
    db: sqlite3.Connection, question_id: int, status: str, answer: dict[str, Any] | None
) -> None:
    db.execute(
        "UPDATE agent_questions SET status = ?, answer = ?, answered_at = datetime('now') "
        "WHERE id = ?",
        (status, json.dumps(answer) if answer is not None else None, question_id),
    )
    db.commit()


def _hydrate_question(row: sqlite3.Row) -> ClarifyingQuestion:
    return ClarifyingQuestion(
        id=row["id"],
        session_id=row["session_id"],
        event_id=row["event_id"],
        external_id=row["external_id"],
        header=row["header"],
        question=row["question"],
        multi_select=bool(row["multi_select"]),
        options=json.loads(row["options"]),
        status=row["status"],
        answer=json.loads(row["answer"]) if row["answer"] else None,
        created_at=row["created_at"],
        answered_at=row["answered_at"],
    )
