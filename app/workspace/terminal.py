from __future__ import annotations

import os
import re
import shlex
import sqlite3
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass

from app.execution.base import ExecutionProvider
from app.security import validate_repository_path
from app.workspace import terminal_models

TMUX_TIMEOUT_SECONDS = 10.0

# Keys a client may send that aren't literal text (sent via `tmux send-keys`
# without `-l`, so tmux interprets the key name). Restricted to a fixed
# allowlist rather than passing through whatever the client sends, since an
# unrestricted key name is interpreted by tmux rather than typed literally.
ALLOWED_KEYS = frozenset(
    {"Enter", "C-c", "Tab", "Escape", "Up", "Down", "Left", "Right", "C-d"}
)

_TAIL_POLL_SECONDS = 0.2
_TAIL_CHUNK_SIZE = 65536

# Strips the ANSI escape sequences tmux emits (colors, cursor movement,
# window title OSC sequences) plus bare carriage returns, since terminal
# output is rendered as plain scrollback text rather than through a real
# terminal emulator (see docs/... plan: no xterm.js dependency).
_ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL or ST
    r"|\x1b\[[0-?]*[ -/]*[@-~]"  # CSI sequences
    r"|\x1b[()#][0-9A-Za-z]"  # charset designation
    r"|\x1b[=>M78]"  # misc single-char escapes
    r"|\r"
)


class TerminalError(RuntimeError):
    """Raised when a tmux command fails or a session is unusable."""


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


@dataclass
class _TailerHandle:
    thread: threading.Thread
    stop_event: threading.Event


def _tail_pipe(
    database_path: str,
    terminal_session_id: int,
    pipe_path: str,
    start_offset: int,
    stop_event: threading.Event,
) -> None:
    db = sqlite3.connect(database_path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        while not stop_event.is_set() and not os.path.exists(pipe_path):
            time.sleep(0.05)
        if stop_event.is_set():
            return

        with open(pipe_path, "rb") as f:
            f.seek(start_offset)
            offset = start_offset
            while not stop_event.is_set():
                chunk = f.read(_TAIL_CHUNK_SIZE)
                if not chunk:
                    time.sleep(_TAIL_POLL_SECONDS)
                    continue
                offset += len(chunk)
                text = strip_ansi(chunk.decode("utf-8", errors="replace"))
                if text:
                    terminal_models.record_output_chunk(db, terminal_session_id, text, offset)
                else:
                    terminal_models.set_terminal_session_pipe_offset(db, terminal_session_id, offset)
    finally:
        db.close()


class _TerminalManager:
    """Tracks the in-process tailer threads that copy tmux pane output into
    the database. Module-level singleton, mirroring the `_handles` dict
    already used by `HostExecutionProvider` for tracked subprocesses."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tailers: dict[int, _TailerHandle] = {}

    def ensure_tailer(
        self, database_path: str, terminal_session_id: int, pipe_path: str, start_offset: int
    ) -> None:
        with self._lock:
            handle = self._tailers.get(terminal_session_id)
            if handle is not None and handle.thread.is_alive():
                return
            stop_event = threading.Event()
            thread = threading.Thread(
                target=_tail_pipe,
                args=(database_path, terminal_session_id, pipe_path, start_offset, stop_event),
                daemon=True,
            )
            self._tailers[terminal_session_id] = _TailerHandle(thread=thread, stop_event=stop_event)
            thread.start()

    def stop_tailer(self, terminal_session_id: int) -> None:
        with self._lock:
            handle = self._tailers.pop(terminal_session_id, None)
        if handle is not None:
            handle.stop_event.set()


_manager = _TerminalManager()


def _run_tmux(execution_provider: ExecutionProvider, context_id: int, args: list[str]):
    return execution_provider.execute(
        ["tmux", *args], options={"context_id": context_id, "timeout": TMUX_TIMEOUT_SECONDS}
    )


def _stderr_text(execution_provider: ExecutionProvider, process) -> str:
    events = execution_provider.stream_output(process.id)
    return "\n".join(
        e.data for e in events if e.event_type == "ProcessOutput" and e.stream == "stderr"
    )


def create_session(
    execution_provider: ExecutionProvider,
    db: sqlite3.Connection,
    database_path: str,
    repo_id: int,
    repo_root: str,
    allowed_roots: tuple[str, ...],
    label: str = "",
) -> terminal_models.TerminalSession:
    repo_root = validate_repository_path(repo_root, allowed_roots or (repo_root,))

    name = f"agentflow-{repo_id}-{uuid.uuid4().hex[:12]}"
    pipe_path = os.path.join(tempfile.gettempdir(), f"{name}.pipe")
    open(pipe_path, "ab").close()

    context = execution_provider.create_context({"working_directory": repo_root})
    try:
        try:
            process = _run_tmux(
                execution_provider,
                context.id,
                ["new-session", "-d", "-s", name, "-x", "220", "-y", "50", "-c", repo_root],
            )
        except OSError as exc:
            raise TerminalError(f"Could not start tmux: {exc}") from exc
        if process.exit_code != 0:
            raise TerminalError(
                f"tmux new-session failed: {_stderr_text(execution_provider, process)}"
            )

        process = _run_tmux(
            execution_provider,
            context.id,
            ["pipe-pane", "-o", "-t", name, f"cat >> {shlex.quote(pipe_path)}"],
        )
        if process.exit_code != 0:
            _run_tmux(execution_provider, context.id, ["kill-session", "-t", name])
            raise TerminalError(
                f"tmux pipe-pane failed: {_stderr_text(execution_provider, process)}"
            )
    finally:
        execution_provider.destroy_context(context.id)

    session_id = terminal_models.create_terminal_session(
        db, repo_id, name, repo_root, pipe_path, label=label
    )
    _manager.ensure_tailer(database_path, session_id, pipe_path, 0)
    return terminal_models.get_terminal_session(db, session_id)


def send_input(
    execution_provider: ExecutionProvider,
    db: sqlite3.Connection,
    session: terminal_models.TerminalSession,
    *,
    text: str | None = None,
    key: str | None = None,
) -> None:
    if session.status != "RUNNING":
        raise TerminalError(f"Terminal session {session.id} is not running (status={session.status})")

    context = execution_provider.create_context({"working_directory": session.working_directory})
    try:
        if text is not None:
            process = _run_tmux(
                execution_provider, context.id, ["send-keys", "-t", session.tmux_session_name, "-l", text]
            )
        elif key is not None:
            if key not in ALLOWED_KEYS:
                raise TerminalError(f"Unsupported key: {key!r}")
            process = _run_tmux(
                execution_provider, context.id, ["send-keys", "-t", session.tmux_session_name, key]
            )
        else:
            raise TerminalError("send_input requires text or key")

        if process.exit_code != 0:
            raise TerminalError(
                f"tmux send-keys failed: {_stderr_text(execution_provider, process)}"
            )
    finally:
        execution_provider.destroy_context(context.id)

    terminal_models.touch_terminal_session(db, session.id)


def ensure_running(
    execution_provider: ExecutionProvider,
    db: sqlite3.Connection,
    database_path: str,
    session: terminal_models.TerminalSession,
) -> bool:
    if session.status == "KILLED":
        return False

    context = execution_provider.create_context({"working_directory": session.working_directory})
    try:
        process = _run_tmux(execution_provider, context.id, ["has-session", "-t", session.tmux_session_name])
        alive = process.exit_code == 0
    finally:
        execution_provider.destroy_context(context.id)

    if not alive:
        if session.status != "LOST":
            terminal_models.set_terminal_session_status(db, session.id, "LOST")
        _manager.stop_tailer(session.id)
        return False

    _manager.ensure_tailer(database_path, session.id, session.pipe_path, session.pipe_offset)
    return True


def kill_session(
    execution_provider: ExecutionProvider,
    db: sqlite3.Connection,
    session: terminal_models.TerminalSession,
) -> None:
    _manager.stop_tailer(session.id)

    context = execution_provider.create_context({"working_directory": session.working_directory})
    try:
        _run_tmux(execution_provider, context.id, ["kill-session", "-t", session.tmux_session_name])
    finally:
        execution_provider.destroy_context(context.id)

    try:
        os.remove(session.pipe_path)
    except OSError:
        pass

    terminal_models.set_terminal_session_status(db, session.id, "KILLED")
