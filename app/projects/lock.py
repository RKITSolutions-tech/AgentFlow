"""Per-project execution lock (docs/RUN_AND_RALPH.md §19).

One ACTIVE row per project (enforced by a partial unique index) says which
Ralph run, pipeline execution or manual Run currently owns the project. The
owner refreshes `heartbeat_at` while it works; a lock whose heartbeat is older
than the stale threshold belongs to a dead owner and is taken over or swept.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from app.runs.models import now

OWNER_TYPES = ("ralph_run", "pipeline_run", "manual_run")
HEARTBEAT_SECONDS = 30
STALE_SECONDS = 360  # 12 missed heartbeats
SWEEP_SECONDS = 60


@dataclass
class ProjectLock:
    id: int
    project_id: int
    owner_type: str
    owner_id: int
    acquired_at: str
    heartbeat_at: str
    released_at: str | None
    status: str

    @property
    def owner(self) -> str:
        return f"{self.owner_type.replace('_', ' ')} #{self.owner_id}"


class LockConflict(ValueError):
    """The project is locked by someone else; `holder` says who."""

    def __init__(self, holder: ProjectLock):
        self.holder = holder
        super().__init__(
            f"Project is locked by {holder.owner} (since {holder.acquired_at[:19]}, "
            f"last heartbeat {holder.heartbeat_at[:19]})"
        )


def _age(stamp: str, at: datetime | None = None) -> float:
    return ((at or datetime.now(timezone.utc)) - datetime.fromisoformat(stamp)).total_seconds()


def _lock(row: sqlite3.Row | None) -> ProjectLock | None:
    return ProjectLock(**dict(row)) if row else None


def current(db: sqlite3.Connection, project_id: int) -> ProjectLock | None:
    """The ACTIVE lock, if any (a stale one is still returned until swept)."""
    return _lock(db.execute(
        "SELECT * FROM project_locks WHERE project_id = ? AND status = 'ACTIVE'", (project_id,)
    ).fetchone())


def is_stale(lock: ProjectLock, threshold: float = STALE_SECONDS, at: datetime | None = None) -> bool:
    return lock.status == "ACTIVE" and _age(lock.heartbeat_at, at) > threshold


def is_lock_stale(db: sqlite3.Connection, project_id: int, threshold: float = STALE_SECONDS) -> bool:
    lock = current(db, project_id)
    return lock is not None and is_stale(lock, threshold)


# Statuses in which an owner still has (or is about to have) a worker running.
_ACTIVE_OWNER = {
    "ralph_run": ("ralph_runs", ("CREATED", "RUNNING", "VERIFYING")),
    "pipeline_run": ("pipeline_executions", ("PENDING", "RUNNING")),
    "manual_run": ("runs", ("CREATED", "RUNNING", "PAUSED")),
}


def owner_finished(db: sqlite3.Connection, lock: ProjectLock) -> bool:
    """True when the owner's own record says it is no longer executing. A worker
    writes its final status just before it releases the lock, so without this a
    quick follow-up request could hit that gap and see a needless conflict."""
    table, active = _ACTIVE_OWNER[lock.owner_type]
    row = db.execute(f"SELECT status FROM {table} WHERE id = ?", (lock.owner_id,)).fetchone()
    return row is not None and row["status"] not in active


def _close(db: sqlite3.Connection, lock_id: int, status: str) -> None:
    db.execute(
        "UPDATE project_locks SET status = ?, released_at = ? WHERE id = ? AND status = 'ACTIVE'",
        (status, now(), lock_id),
    )


def acquire(
    db: sqlite3.Connection, project_id: int, owner_type: str, owner_id: int,
    stale_after: float = STALE_SECONDS,
) -> ProjectLock:
    """Take the project lock, or raise LockConflict.

    The current owner asking again just refreshes its heartbeat (a steering
    request must not fail on its own run's lock); a stale lock is taken over.
    """
    if owner_type not in OWNER_TYPES:
        raise ValueError(f"Unknown lock owner type {owner_type!r}")
    for _ in range(3):  # a rival may release/take between our read and insert
        held = current(db, project_id)
        if held is not None:
            if (held.owner_type, held.owner_id) == (owner_type, owner_id):
                db.execute("UPDATE project_locks SET heartbeat_at = ? WHERE id = ?", (now(), held.id))
                db.commit()
                return current(db, project_id)
            if owner_finished(db, held):
                _close(db, held.id, "RELEASED")
            elif not is_stale(held, stale_after):
                raise LockConflict(held)
            else:
                _close(db, held.id, "STALE")
        stamp = now()
        try:
            db.execute(
                "INSERT INTO project_locks (project_id, owner_type, owner_id, acquired_at, heartbeat_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (project_id, owner_type, owner_id, stamp, stamp),
            )
            db.commit()
            return current(db, project_id)
        except sqlite3.IntegrityError:
            db.rollback()
    raise LockConflict(current(db, project_id))


def release(db: sqlite3.Connection, project_id: int, owner_type: str, owner_id: int) -> bool:
    """Release the lock if this owner holds it. Returns whether anything changed."""
    cur = db.execute(
        "UPDATE project_locks SET status = 'RELEASED', released_at = ? "
        "WHERE project_id = ? AND owner_type = ? AND owner_id = ? AND status = 'ACTIVE'",
        (now(), project_id, owner_type, owner_id),
    )
    db.commit()
    return cur.rowcount > 0


def refresh_heartbeat(db: sqlite3.Connection, project_id: int, owner_type: str, owner_id: int) -> bool:
    """False if the owner no longer holds the lock (it went stale and was swept)."""
    cur = db.execute(
        "UPDATE project_locks SET heartbeat_at = ? "
        "WHERE project_id = ? AND owner_type = ? AND owner_id = ? AND status = 'ACTIVE'",
        (now(), project_id, owner_type, owner_id),
    )
    db.commit()
    return cur.rowcount > 0


def detect_and_release_stale_locks(db: sqlite3.Connection, threshold: float = STALE_SECONDS) -> list[ProjectLock]:
    swept = []
    for row in db.execute("SELECT * FROM project_locks WHERE status = 'ACTIVE'").fetchall():
        lock = _lock(row)
        if is_stale(lock, threshold):
            _close(db, lock.id, "STALE")
            swept.append(lock)
    db.commit()
    return swept


def release_all_active(db: sqlite3.Connection) -> int:
    """At startup no worker exists yet, so every ACTIVE lock is an orphan."""
    cur = db.execute(
        "UPDATE project_locks SET status = 'STALE', released_at = ? WHERE status = 'ACTIVE'", (now(),)
    )
    db.commit()
    return cur.rowcount


def history(db: sqlite3.Connection, project_id: int, limit: int = 10) -> list[ProjectLock]:
    return [_lock(r) for r in db.execute(
        "SELECT * FROM project_locks WHERE project_id = ? ORDER BY id DESC LIMIT ?", (project_id, limit)
    )]




class Heartbeat:
    """Refreshes a held lock from a background thread until stopped, then releases it.

    Uses its own connection: a SQLite connection belongs to the thread that
    opened it, and the worker thread is busy running the work.
    """

    def __init__(self, db_path: str, project_id: int, owner_type: str, owner_id: int, interval: float = HEARTBEAT_SECONDS):
        self._args = (project_id, owner_type, owner_id)
        self._path = db_path
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, daemon=True, name=f"lock-{owner_type}-{owner_id}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _beat(self) -> None:
        db = self._connect()
        try:
            while not self._stop.wait(self._interval):
                try:
                    refresh_heartbeat(db, *self._args)
                except sqlite3.Error:
                    pass  # try again next interval
        finally:
            db.close()

    def start(self) -> "Heartbeat":
        self._thread.start()
        return self

    def stop(self, release_lock: bool = True) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        if release_lock:
            db = self._connect()
            try:
                release(db, *self._args)
            finally:
                db.close()


def acquire_for_worker(
    db: sqlite3.Connection, db_path: str, project_id: int, owner_type: str, owner_id: int,
    interval: float = HEARTBEAT_SECONDS,
) -> Heartbeat:
    """Acquire synchronously (so the caller can report LockConflict) and hand
    back a started Heartbeat the worker stops in its `finally`."""
    acquire(db, project_id, owner_type, owner_id)
    return Heartbeat(db_path, project_id, owner_type, owner_id, interval).start()


class Sweeper:
    """Periodically frees locks whose owner stopped heartbeating."""

    def __init__(self, db_path: str, interval: float = SWEEP_SECONDS, threshold: float = STALE_SECONDS):
        self._path, self._interval, self._threshold = db_path, interval, threshold
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="lock-sweeper")

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            db = sqlite3.connect(self._path, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                detect_and_release_stale_locks(db, self._threshold)
            except sqlite3.Error:
                pass
            finally:
                db.close()

    def start(self) -> "Sweeper":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
