"""Project execution locks (docs/RUN_AND_RALPH.md §19, §22).

An ACTIVE row says which Ralph run, pipeline execution or manual Run currently
owns a scope of a project: the whole project (`repository_id` NULL, the default)
or one repository, for projects whose `lock_scope` is `repository`. Two locks
conflict when their scopes overlap (a whole-project lock overlaps everything).
The owner refreshes `heartbeat_at` while it works; a lock whose heartbeat is
older than the stale threshold belongs to a dead owner and is taken over or swept.

By default a run that cannot get the lock fails. With `wait` it joins a queue
(`project_lock_queue`, first come first served among overlapping scopes) and its
worker thread takes the lock when it is free.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.runs.models import now

OWNER_TYPES = ("ralph_run", "pipeline_run", "manual_run")
HEARTBEAT_SECONDS = 30
STALE_SECONDS = 360  # 12 missed heartbeats
SWEEP_SECONDS = 60
WAIT_SECONDS = 3600  # how long a queued run waits before giving up
POLL_SECONDS = 1.0  # how often a queued run looks at the lock
WAITER_STALE_SECONDS = 60  # a waiter that has not polled for this long is dead


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
    repository_id: int | None = None

    @property
    def owner(self) -> str:
        return f"{self.owner_type.replace('_', ' ')} #{self.owner_id}"

    @property
    def scope(self) -> str:
        return "whole project" if self.repository_id is None else f"repository #{self.repository_id}"


class LockConflict(ValueError):
    """The scope is locked by someone else (`holder` says who), or others are queued ahead."""

    def __init__(self, holder: ProjectLock | None, message: str | None = None):
        self.holder = holder
        if message is None and holder is not None:
            message = (
                f"Project is locked by {holder.owner}"
                f"{'' if holder.repository_id is None else f' on {holder.scope}'} (since {holder.acquired_at[:19]}, "
                f"last heartbeat {holder.heartbeat_at[:19]})"
            )
        super().__init__(message or "Project is busy")


def overlaps(a: int | None, b: int | None) -> bool:
    """Scopes conflict when either is the whole project (None) or they name the same repository."""
    return a is None or b is None or a == b


def _age(stamp: str, at: datetime | None = None) -> float:
    return ((at or datetime.now(timezone.utc)) - datetime.fromisoformat(stamp)).total_seconds()


def _lock(row: sqlite3.Row | None) -> ProjectLock | None:
    return ProjectLock(**dict(row)) if row else None


def active_locks(db: sqlite3.Connection, project_id: int) -> list[ProjectLock]:
    return [_lock(r) for r in db.execute(
        "SELECT * FROM project_locks WHERE project_id = ? AND status = 'ACTIVE' ORDER BY id", (project_id,))]


def current(db: sqlite3.Connection, project_id: int, repository_id: int | None = None) -> ProjectLock | None:
    """The ACTIVE lock that overlaps this scope, if any (a stale one is still
    returned until swept). No `repository_id` asks about the whole project, so
    any lock counts."""
    return next((l for l in active_locks(db, project_id) if overlaps(l.repository_id, repository_id)), None)


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


def _begin(db: sqlite3.Connection) -> None:
    """Take SQLite's write lock up front, so reading the rivals and inserting our
    row are one step: two scopes that overlap without being equal (project vs
    repository) are not covered by the unique index."""
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")


def acquire(
    db: sqlite3.Connection, project_id: int, owner_type: str, owner_id: int,
    stale_after: float = STALE_SECONDS, repository_id: int | None = None, ticket_id: int | None = None,
) -> ProjectLock:
    """Take the lock for a scope, or raise LockConflict.

    The current owner asking again just refreshes its heartbeat (a steering
    request must not fail on its own run's lock); a stale lock is taken over.
    Queued waiters go first: without a `ticket_id` any overlapping waiter blocks
    us, with one only those queued before it do.
    """
    if owner_type not in OWNER_TYPES:
        raise ValueError(f"Unknown lock owner type {owner_type!r}")
    for _ in range(3):  # a rival may release/take between our read and insert
        _begin(db)
        try:
            expire_stale_waiters(db)
            rivals = [l for l in active_locks(db, project_id) if overlaps(l.repository_id, repository_id)]
            for held in rivals:
                if (held.owner_type, held.owner_id) == (owner_type, owner_id):
                    db.execute("UPDATE project_locks SET heartbeat_at = ? WHERE id = ?", (now(), held.id))
                    db.commit()
                    return current(db, project_id, held.repository_id)
            blocking = None
            for held in rivals:
                if owner_finished(db, held):
                    _close(db, held.id, "RELEASED")
                elif is_stale(held, stale_after):
                    _close(db, held.id, "STALE")
                elif blocking is None:
                    blocking = held
            if blocking is not None:
                db.commit()
                raise LockConflict(blocking)
            ahead = _waiters_ahead(db, project_id, repository_id, ticket_id)
            if ahead:
                db.commit()
                raise LockConflict(None, f"{ahead} run(s) are queued for this project ahead of {owner_type.replace('_', ' ')} #{owner_id}")
            stamp = now()
            db.execute(
                "INSERT INTO project_locks (project_id, owner_type, owner_id, repository_id, acquired_at, heartbeat_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, owner_type, owner_id, repository_id, stamp, stamp),
            )
            if ticket_id is not None:
                db.execute("UPDATE project_lock_queue SET status = 'GRANTED' WHERE id = ?", (ticket_id,))
            db.commit()
            return current(db, project_id, repository_id)
        except sqlite3.IntegrityError:
            db.rollback()
    raise LockConflict(current(db, project_id, repository_id))


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
    expire_stale_waiters(db)
    return swept


def release_all_active(db: sqlite3.Connection) -> int:
    """At startup no worker exists yet, so every ACTIVE lock is an orphan, and so
    is every queued waiter (its thread died with the process)."""
    cur = db.execute(
        "UPDATE project_locks SET status = 'STALE', released_at = ? WHERE status = 'ACTIVE'", (now(),)
    )
    db.execute("UPDATE project_lock_queue SET status = 'EXPIRED' WHERE status = 'WAITING'")
    db.commit()
    return cur.rowcount


def history(db: sqlite3.Connection, project_id: int, limit: int = 10) -> list[ProjectLock]:
    return [_lock(r) for r in db.execute(
        "SELECT * FROM project_locks WHERE project_id = ? ORDER BY id DESC LIMIT ?", (project_id, limit)
    )]




# -- queue (wait instead of fail) -----------------------------------------------------------------


def expire_stale_waiters(db: sqlite3.Connection, threshold: float = WAITER_STALE_SECONDS) -> int:
    """A waiter polls every POLL_SECONDS; one silent for `threshold` has lost its thread."""
    n = 0
    for row in db.execute("SELECT id, polled_at FROM project_lock_queue WHERE status = 'WAITING'").fetchall():
        if _age(row["polled_at"]) > threshold:
            db.execute("UPDATE project_lock_queue SET status = 'EXPIRED' WHERE id = ?", (row["id"],))
            n += 1
    if n:
        db.commit()
    return n


def _waiters_ahead(db: sqlite3.Connection, project_id: int, repository_id: int | None, ticket_id: int | None) -> int:
    rows = db.execute(
        "SELECT id, repository_id FROM project_lock_queue WHERE project_id = ? AND status = 'WAITING'", (project_id,)
    ).fetchall()
    return sum(
        1 for r in rows
        if overlaps(r["repository_id"], repository_id) and (ticket_id is None or r["id"] < ticket_id)
    )


def enqueue(db: sqlite3.Connection, project_id: int, owner_type: str, owner_id: int, repository_id: int | None = None) -> int:
    """Join the queue (once per owner). Returns the ticket id; order is by id."""
    row = db.execute(
        "SELECT id FROM project_lock_queue WHERE project_id = ? AND owner_type = ? AND owner_id = ? AND status = 'WAITING'",
        (project_id, owner_type, owner_id),
    ).fetchone()
    if row:
        return row["id"]
    stamp = now()
    cur = db.execute(
        "INSERT INTO project_lock_queue (project_id, repository_id, owner_type, owner_id, enqueued_at, polled_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (project_id, repository_id, owner_type, owner_id, stamp, stamp),
    )
    db.commit()
    return cur.lastrowid


def cancel_ticket(db: sqlite3.Connection, ticket_id: int, status: str = "CANCELLED") -> None:
    db.execute("UPDATE project_lock_queue SET status = ? WHERE id = ? AND status = 'WAITING'", (status, ticket_id))
    db.commit()


def waiting(db: sqlite3.Connection, project_id: int) -> list[dict]:
    """Who is queued for the project, in order, with a 1-based position."""
    rows = db.execute(
        "SELECT * FROM project_lock_queue WHERE project_id = ? AND status = 'WAITING' ORDER BY id", (project_id,)
    ).fetchall()
    return [{**dict(r), "position": i, "owner": f"{r['owner_type'].replace('_', ' ')} #{r['owner_id']}"}
            for i, r in enumerate(rows, start=1)]


def cancel_waiting_for(db: sqlite3.Connection, owner_type: str, owner_id: int) -> bool:
    """Take an owner out of the queue (its run was stopped before it got the lock)."""
    cur = db.execute(
        "UPDATE project_lock_queue SET status = 'CANCELLED' WHERE owner_type = ? AND owner_id = ? AND status = 'WAITING'",
        (owner_type, owner_id),
    )
    db.commit()
    return cur.rowcount > 0


def scope_for(db: sqlite3.Connection, project_id: int, repository_id: int | None) -> int | None:
    """The repository to lock: only for projects that opted into `repository` scope."""
    row = db.execute("SELECT lock_scope FROM projects WHERE id = ?", (project_id,)).fetchone()
    return repository_id if row and row["lock_scope"] == "repository" else None


@dataclass
class Ticket:
    """A place in the queue. `wait()` blocks (in the run's worker thread) until the lock is ours."""
    id: int
    db_path: str
    project_id: int
    owner_type: str
    owner_id: int
    repository_id: int | None
    interval: float = HEARTBEAT_SECONDS

    def wait(
        self, should_stop: Callable[[], bool] = lambda: False, timeout: float | None = None,
        poll: float | None = None,
    ) -> "Heartbeat":
        """Returns a started Heartbeat once acquired. Raises LockConflict if the
        wait times out or `should_stop()` says the run was stopped meanwhile."""
        timeout = WAIT_SECONDS if timeout is None else timeout
        poll = POLL_SECONDS if poll is None else poll
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        deadline = time.monotonic() + timeout
        try:
            while True:
                if should_stop():
                    cancel_ticket(db, self.id)
                    raise LockConflict(None, "Stopped while waiting for the project")
                try:
                    acquire(db, self.project_id, self.owner_type, self.owner_id,
                            repository_id=self.repository_id, ticket_id=self.id)
                    break
                except LockConflict as exc:
                    if time.monotonic() >= deadline:
                        cancel_ticket(db, self.id, "EXPIRED")
                        raise LockConflict(exc.holder, f"Gave up waiting after {int(timeout)}s: {exc}") from None
                    db.execute("UPDATE project_lock_queue SET polled_at = ? WHERE id = ?", (now(), self.id))
                    db.commit()
                    time.sleep(poll)
        finally:
            db.close()
        return Heartbeat(self.db_path, self.project_id, self.owner_type, self.owner_id, self.interval).start()


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
    interval: float = HEARTBEAT_SECONDS, repository_id: int | None = None,
) -> Heartbeat:
    """Acquire synchronously (so the caller can report LockConflict) and hand
    back a started Heartbeat the worker stops in its `finally`."""
    acquire(db, project_id, owner_type, owner_id, repository_id=repository_id)
    return Heartbeat(db_path, project_id, owner_type, owner_id, interval).start()


def begin(
    db: sqlite3.Connection, db_path: str, project_id: int, owner_type: str, owner_id: int,
    repository_id: int | None = None, wait: bool = False, interval: float = HEARTBEAT_SECONDS,
) -> tuple[Heartbeat | None, Ticket | None]:
    """Try to take the lock now. Returns (heartbeat, None) when it is ours; with
    `wait`, a busy project returns (None, ticket) and the worker calls
    `ticket.wait()`; without it, LockConflict propagates. The queue position is
    fixed here, in request order, not when the thread happens to start."""
    try:
        return acquire_for_worker(db, db_path, project_id, owner_type, owner_id, interval, repository_id), None
    except LockConflict:
        if not wait:
            raise
    ticket_id = enqueue(db, project_id, owner_type, owner_id, repository_id)
    return None, Ticket(ticket_id, db_path, project_id, owner_type, owner_id, repository_id, interval)


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
