"""Server side of the chat composer: slash commands, @file mentions,
attachments and scheduled messages (docs/CLOUDCLI_GAP_ANALYSIS.md G6)."""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.agents import models
from app.agents.codex import PERMISSION_MODES
from app.security import PathNotAllowedError, validate_repository_path

# -- attachments -------------------------------------------------------------

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 5
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ComposerError(ValueError):
    """A composer request was rejected; the message is user-facing."""


def attachment_dir(database_path: str, session_id: int) -> str:
    """Attachments live beside the database, never inside a repository."""
    base = os.path.dirname(os.path.abspath(database_path))
    return os.path.join(base, "attachments", str(session_id))


def save_attachment(database_path: str, session_id: int, filename: str, stream) -> dict:
    name = _UNSAFE_NAME.sub("_", os.path.basename((filename or "").replace("\\", "/"))).strip("._")
    if not name:
        raise ComposerError("The attachment needs a file name.")
    data = stream.read(MAX_ATTACHMENT_SIZE + 1)
    if len(data) > MAX_ATTACHMENT_SIZE:
        raise ComposerError(
            f"{name!r} is larger than the {MAX_ATTACHMENT_SIZE // (1024 * 1024)} MB attachment limit."
        )
    directory = attachment_dir(database_path, session_id)
    os.makedirs(directory, exist_ok=True)
    stored = f"{uuid.uuid4().hex[:8]}-{name}"
    with open(os.path.join(directory, stored), "wb") as fh:
        fh.write(data)
    return {"id": stored, "name": name, "size": len(data), "is_image": is_image(name)}


def is_image(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS


def resolve_attachments(database_path: str, session_id: int, ids: list[str]) -> list[str]:
    """Absolute paths for previously uploaded attachment ids of this session."""
    if len(ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise ComposerError(f"At most {MAX_ATTACHMENTS_PER_MESSAGE} attachments per message.")
    directory = attachment_dir(database_path, session_id)
    paths = []
    for attachment_id in ids:
        # Ids are our own `<hex>-<name>` strings; anything with a separator is forged.
        if attachment_id != os.path.basename(attachment_id):
            raise ComposerError("Unknown attachment.")
        path = os.path.join(directory, attachment_id)
        if not os.path.isfile(path):
            raise ComposerError("Unknown attachment.")
        paths.append(path)
    return paths


def compose_prompt(prompt: str, attachment_paths: list[str]) -> tuple[str, list[str]]:
    """Return the prompt text to send and the image paths to pass natively.

    Images go to the agent as images; other files are referenced by path in
    the prompt so the agent can read them itself.
    """
    images = [p for p in attachment_paths if is_image(p)]
    others = [p for p in attachment_paths if not is_image(p)]
    if others:
        prompt = prompt.rstrip() + "\n\nAttached files:\n" + "\n".join(f"- {p}" for p in others)
    return prompt, images


# -- @ file mentions ---------------------------------------------------------

_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"})
MAX_MENTION_RESULTS = 20
_MAX_WALK_ENTRIES = 20000


def search_mentions(working_directory: str, allowed_roots: tuple[str, ...], query: str) -> list[str]:
    """Repo-relative file paths whose path contains ``query`` (case-insensitive).

    Name matches rank ahead of directory-only matches. The walk is capped so a
    huge tree cannot stall a keystroke-driven request.
    """
    try:
        root = validate_repository_path(working_directory, allowed_roots)
    except PathNotAllowedError:
        return []
    needle = query.strip().lower()
    by_name, by_path = [], []
    seen = 0
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(names):
            seen += 1
            rel = os.path.relpath(os.path.join(current, name), root)
            lowered = rel.lower()
            if needle in name.lower():
                by_name.append(rel)
            elif needle in lowered:
                by_path.append(rel)
            if len(by_name) >= MAX_MENTION_RESULTS or seen >= _MAX_WALK_ENTRIES:
                return by_name[:MAX_MENTION_RESULTS]
    return (by_name + by_path)[:MAX_MENTION_RESULTS]


# -- slash commands ----------------------------------------------------------


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    args: str = ""
    capability: str | None = None


COMMANDS = (
    Command("help", "List the available commands"),
    Command("rename", "Rename this session", "<title>"),
    Command("model", "Use a model for the next message (\"default\" clears)", "<model>", "model_selection"),
    Command("mode", "Set the permission mode: " + ", ".join(PERMISSION_MODES), "<mode>", "permission_modes"),
    Command("usage", "Show token usage for this session", "", "token_usage"),
    Command("fork", "Fork this session", "", "fork"),
    Command("archive", "Archive this session"),
    Command("stop", "Stop this session"),
)


def available_commands(capabilities: frozenset[str]) -> list[Command]:
    return [c for c in COMMANDS if c.capability is None or c.capability in capabilities]


def parse_command(text: str) -> tuple[str, str] | None:
    """``("name", "args")`` for ``/name args``; ``None`` for plain text.

    ``//text`` is the escape for a message that genuinely starts with a slash.
    """
    text = text.strip()
    if not text.startswith("/") or text.startswith("//"):
        return None
    name, _, arg = text[1:].partition(" ")
    return name.lower(), arg.strip()


# -- scheduled messages ------------------------------------------------------

_DB_TIME = "%Y-%m-%d %H:%M:%S"
MAX_SCHEDULE_AHEAD_DAYS = 365


def parse_send_at(value: str) -> str:
    """Normalise an ISO-8601 timestamp to the UTC ``YYYY-MM-DD HH:MM:SS`` used in the DB.

    A timestamp with no offset is taken as UTC (the browser always sends one).
    """
    try:
        moment = datetime.fromisoformat((value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        raise ComposerError("Enter a valid date and time.") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if moment <= now:
        raise ComposerError("Pick a time in the future.")
    if (moment - now).days > MAX_SCHEDULE_AHEAD_DAYS:
        raise ComposerError("That is too far ahead.")
    return moment.strftime(_DB_TIME)


def schedule_message(db: sqlite3.Connection, session_id: int, content: str, send_at: str) -> int:
    content = content.strip()
    if not content:
        raise ComposerError("A scheduled message needs some text.")
    cur = db.execute(
        "INSERT INTO scheduled_messages (session_id, content, send_at) VALUES (?, ?, ?)",
        (session_id, content, parse_send_at(send_at)),
    )
    db.commit()
    return cur.lastrowid


def list_scheduled(db: sqlite3.Connection, session_id: int, pending_only: bool = True) -> list[dict]:
    where = "AND status = 'PENDING'" if pending_only else ""
    rows = db.execute(
        f"SELECT * FROM scheduled_messages WHERE session_id = ? {where} ORDER BY send_at, id",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def cancel_scheduled(db: sqlite3.Connection, session_id: int, message_id: int) -> bool:
    cur = db.execute(
        "UPDATE scheduled_messages SET status = 'CANCELLED' "
        "WHERE id = ? AND session_id = ? AND status = 'PENDING'",
        (message_id, session_id),
    )
    db.commit()
    return cur.rowcount == 1


def _due(db: sqlite3.Connection, session_id: int | None) -> list[sqlite3.Row]:
    where = "AND session_id = ?" if session_id is not None else ""
    args = (session_id,) if session_id is not None else ()
    return db.execute(
        "SELECT * FROM scheduled_messages WHERE status = 'PENDING' "
        f"AND send_at <= datetime('now') {where} ORDER BY send_at, id",
        args,
    ).fetchall()


def has_due(db: sqlite3.Connection, session_id: int) -> bool:
    return bool(_due(db, session_id))


def dispatch_due(db: sqlite3.Connection, adapter_for, session_id: int | None = None) -> int:
    """Deliver due messages; returns how many were sent.

    There is no background scheduler: this runs when a session is polled by
    an open chat page and from ``flask sessions dispatch-scheduled``. A message whose
    session is mid-turn stays pending and goes out on a later call, one
    message per session per call so turns do not pile up.
    """
    sent = 0
    busy: set[int] = set()
    for row in _due(db, session_id):
        sid = row["session_id"]
        if sid in busy:
            continue
        session = models.get_agent_session(db, sid)
        if session is None:
            continue
        if session.status in models.ACTIVE_STATUSES:
            busy.add(sid)
            continue
        try:
            adapter_for(session).send(sid, row["content"])
        except Exception as exc:  # noqa: BLE001 - recorded on the row for the user
            db.execute(
                "UPDATE scheduled_messages SET status = 'FAILED', error = ? WHERE id = ?",
                (str(exc), row["id"]),
            )
        else:
            db.execute(
                "UPDATE scheduled_messages SET status = 'SENT', sent_at = datetime('now') "
                "WHERE id = ?",
                (row["id"],),
            )
            sent += 1
            busy.add(sid)
        db.commit()
    return sent
