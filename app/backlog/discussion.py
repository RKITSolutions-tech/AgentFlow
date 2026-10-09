"""Item-scoped discussions (task 58): an ordinary interactive GENERAL session
started from a Backlog item's page with the item preloaded as context, so a
person can talk an item through with an agent.

The session is the same machinery as any other (app/sessions/starter.py); it
only carries `backlog_item_id` in its metadata, which is how the item page
lists its discussions and how the session view offers "Update the item from
the selected messages". The agent never writes the item itself: a person picks
the reply to apply and the change goes through `persistence.update_item`/
`transition`, with a triage-history note marking `agent_discussion` as the
source (an INBOX item moves to TRIAGED, as a refinement is triage).
"""
from __future__ import annotations

import re
import sqlite3

from app.agents import models as agent_models
from app.agents.models import AgentSession
from app.backlog import persistence
from app.backlog.models import BacklogItem

HISTORY_LIMIT = 20
CONTEXT_TEXT_MAX = 6000
SOURCE = "agent_discussion"
_PROPOSAL = re.compile(r"```backlog-item\s*\n(.*?)\n```", re.S)
APPLY_MODES = ("replace", "append")


def item_context(db: sqlite3.Connection, item: BacklogItem) -> str:
    """Starting-prompt context: the item's text, attachments and triage history."""
    lines = [
        "[System Context]",
        f"This session discusses Backlog item #{item.id} of this project. Help refine it: clarify the "
        "problem, scope and acceptance criteria. When you propose a revised item description, put "
        "the complete new text in a fenced block that starts with ```backlog-item so the developer "
        "can apply it to the item.",
        "",
        f"Title: {item.title or '(none)'}",
        f"Status: {item.status}" + (f" | Priority: {item.priority}" if item.priority else ""),
        "Text:",
        (item.text or "(empty)")[:CONTEXT_TEXT_MAX],
    ]
    attachments = persistence.list_attachments(db, item.id)
    if attachments:
        lines += ["", "Attachments:"] + [f"- {a.kind.lower()}: {a.name} ({a.path})" for a in attachments]
    history = persistence.list_history(db, item.id)[-HISTORY_LIMIT:]
    if history:
        lines += ["", "Triage history (oldest first):"]
        for entry in history:
            change = f"{entry.old_status or '-'} -> {entry.new_status}"
            note = f": {entry.notes}" if entry.notes else ""
            lines.append(f"- {entry.changed_at} {change}{note}")
    return "\n".join(lines)


def discussions_for_item(db: sqlite3.Connection, item_id: int) -> list[AgentSession]:
    rows = db.execute(
        "SELECT * FROM agent_sessions WHERE json_extract(metadata, '$.backlog_item_id') = ? "
        "ORDER BY last_activity_at DESC, id DESC",
        (item_id,),
    ).fetchall()
    return [agent_models._hydrate_session(r) for r in rows]


def extract_proposal(text: str) -> str:
    """The ```backlog-item block of an agent reply if it has one, else the reply."""
    match = _PROPOSAL.search(text or "")
    return (match.group(1) if match else text or "").strip()


def proposal_text(db: sqlite3.Connection, session_id: int, event_ids: list[int]) -> str:
    """The text the selected agent messages propose for the item, in order.
    Only this session's AgentText events count; raises ValueError otherwise."""
    if not event_ids:
        raise ValueError("Select the agent message(s) to apply.")
    events = {e.id: e for e in agent_models.list_agent_events(db, session_id) if e.event_type == "AgentText"}
    missing = [i for i in event_ids if i not in events]
    if missing:
        raise ValueError("Only this discussion's agent replies can be applied to the item.")
    return "\n\n".join(extract_proposal(events[i].data) for i in sorted(set(event_ids))).strip()


def apply_to_item(db: sqlite3.Connection, item: BacklogItem, session_id: int, text: str, mode: str) -> str:
    """Write agreed text to the item with an audit trail. Returns the new status."""
    if mode not in APPLY_MODES:
        raise ValueError("Choose to replace or append to the item text.")
    if not text.strip():
        raise ValueError("The selected messages have no text to apply.")
    new_text = text.strip() if mode == "replace" or not item.text.strip() else f"{item.text.rstrip()}\n\n{text.strip()}"
    persistence.update_item(db, item.id, text=new_text)
    note = f"{SOURCE}: item text {'replaced' if mode == 'replace' else 'extended'} from chat session #{session_id}"
    if item.status == "INBOX":
        persistence.transition(db, item.id, "TRIAGED", notes=note, changed_by=SOURCE)
        return "TRIAGED"
    persistence.record_note(db, item.id, note, SOURCE)
    return item.status


def session_item_id(session: AgentSession) -> int | None:
    value = session.metadata.get("backlog_item_id")
    return int(value) if isinstance(value, (int, str)) and str(value).isdigit() else None


def attach_upload(app_config, db: sqlite3.Connection, session: AgentSession, item_id: int,
                  stored_id: str, name: str) -> int | None:
    """Copy a file uploaded into an item discussion into the item's own
    attachments (`<artifact root>/backlog/<project>/<item>/`). Returns the new
    attachment id, or None if the item is gone or the copy is refused."""
    from app.backlog import attachments
    from app.runs import artifacts as run_artifacts
    from app.sessions import composer

    item = persistence.get_item(db, item_id)
    if item is None or item.project_id != session.project_id:
        return None
    try:
        path = composer.resolve_attachments(app_config["DATABASE_PATH"], session.id, [stored_id])[0]
        with open(path, "rb") as fh:
            content = fh.read()
        return attachments.store_attachment(
            db, run_artifacts.artifact_root(app_config), session.project_id, item_id, name, content,
        )
    except (OSError, ValueError, composer.ComposerError, run_artifacts.ArtifactPathError):
        return None
