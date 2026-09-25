"""Notifications for sessions blocked on a human (docs/CLOUDCLI_GAP_ANALYSIS.md G2).

AgentFlow has a single trusted user, so preferences are global: one on/off per
(kind, channel), enabled by default. Each enabled channel gets its own row when
something happens, so history shows what was raised where:

- ``in_app``: shown as an unread badge and in the notification list.
- ``browser``: shown through the browser Notifications API by any open page
  that has permission. Server-side there is no push service, so this only
  reaches an open tab; ``in_app`` is the fallback when it is denied.

Email is not implemented; it would need SMTP configuration.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

KINDS = {
    "blocked_on_question": "Agent asked a question",
    "awaiting_permission": "Agent needs approval",
}
CHANNELS = {
    "in_app": "In-app",
    "browser": "Browser",
}


@dataclass
class Notification:
    id: int
    session_id: int
    kind: str
    channel: str
    message: str
    created_at: str
    read_at: str | None
    delivered_at: str | None


def is_enabled(db: sqlite3.Connection, kind: str, channel: str) -> bool:
    row = db.execute(
        "SELECT enabled FROM notification_preferences WHERE kind = ? AND channel = ?",
        (kind, channel),
    ).fetchone()
    return True if row is None else bool(row["enabled"])


def get_preferences(db: sqlite3.Connection) -> dict[str, dict[str, bool]]:
    return {k: {c: is_enabled(db, k, c) for c in CHANNELS} for k in KINDS}


def set_preference(db: sqlite3.Connection, kind: str, channel: str, enabled: bool) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind {kind!r}")
    if channel not in CHANNELS:
        raise ValueError(f"unknown notification channel {channel!r}")
    db.execute(
        "INSERT INTO notification_preferences (kind, channel, enabled) VALUES (?, ?, ?) "
        "ON CONFLICT(kind, channel) DO UPDATE SET enabled = excluded.enabled",
        (kind, channel, int(enabled)),
    )
    db.commit()


def notify(
    db: sqlite3.Connection, session_id: int, kind: str, message: str
) -> list[Notification]:
    """Record a notification on every channel enabled for ``kind``."""
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind {kind!r}")
    created = []
    for channel in CHANNELS:
        if not is_enabled(db, kind, channel):
            continue
        cur = db.execute(
            "INSERT INTO notifications (session_id, kind, channel, message) VALUES (?, ?, ?, ?)",
            (session_id, kind, channel, message),
        )
        created.append(cur.lastrowid)
    db.commit()
    return [get_notification(db, n) for n in created]


def get_notification(db: sqlite3.Connection, notification_id: int) -> Notification | None:
    row = db.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
    return Notification(**dict(row)) if row else None


def list_notifications(
    db: sqlite3.Connection, channel: str = "in_app", unread_only: bool = False, limit: int = 100
) -> list[Notification]:
    sql = "SELECT * FROM notifications WHERE channel = ?"
    if unread_only:
        sql += " AND read_at IS NULL"
    rows = db.execute(sql + " ORDER BY id DESC LIMIT ?", (channel, limit)).fetchall()
    return [Notification(**dict(r)) for r in rows]


def unread_count(db: sqlite3.Connection) -> int:
    return db.execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE channel = 'in_app' AND read_at IS NULL"
    ).fetchone()["n"]


def mark_read(db: sqlite3.Connection, notification_id: int) -> None:
    db.execute(
        "UPDATE notifications SET read_at = datetime('now') WHERE id = ? AND read_at IS NULL",
        (notification_id,),
    )
    db.commit()


def mark_all_read(db: sqlite3.Connection) -> None:
    db.execute("UPDATE notifications SET read_at = datetime('now') WHERE read_at IS NULL")
    db.commit()


def mark_session_read(db: sqlite3.Connection, session_id: int) -> None:
    """Clear a session's notifications once its blocker is resolved."""
    db.execute(
        "UPDATE notifications SET read_at = datetime('now') "
        "WHERE session_id = ? AND read_at IS NULL",
        (session_id,),
    )
    db.commit()


def take_undelivered_browser(db: sqlite3.Connection) -> list[Notification]:
    """Return browser notifications not yet shown, marking them delivered."""
    rows = db.execute(
        "SELECT * FROM notifications WHERE channel = 'browser' AND delivered_at IS NULL "
        "AND read_at IS NULL ORDER BY id"
    ).fetchall()
    if rows:
        db.execute(
            "UPDATE notifications SET delivered_at = datetime('now') WHERE id IN (%s)"
            % ",".join("?" * len(rows)),
            [r["id"] for r in rows],
        )
        db.commit()
    return [Notification(**{**dict(r), "delivered_at": "delivered"}) for r in rows]
