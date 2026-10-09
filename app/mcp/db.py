from __future__ import annotations

import sqlite3
import threading


def connect(database_path: str) -> sqlite3.Connection:
    """Standalone connection for the MCP server subprocess, which has no Flask
    app context (`app/db.py`'s `get_db()` needs `g`/`current_app`). Mirrors
    that factory's settings -- `timeout=30` and WAL match every other
    connection opened outside a request (`app/projects/lock.py`,
    `app/pipelines/manager.py`, `app/execution/host.py`)."""
    conn = sqlite3.connect(database_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


class ConnectionPool:
    """One sqlite3 connection per thread, opened lazily.

    The `mcp` SDK runs each synchronous tool call via `anyio.to_thread.run_sync`,
    which draws from a worker-thread pool rather than one fixed thread -- a
    single shared connection trips sqlite3's same-thread check. Mirrors
    `HostExecutionProvider._db()` (`app/execution/host.py`), which hits the
    identical problem for the same reason (its own process/event calls also
    run off Flask's request thread).
    """

    def __init__(self, database_path: str):
        self._database_path = database_path
        self._local = threading.local()

    def get(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = connect(self._database_path)
            self._local.conn = conn
        return conn
