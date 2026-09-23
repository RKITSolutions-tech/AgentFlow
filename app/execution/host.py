from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, IO

from app.execution import models
from app.execution.base import ExecutionProvider
from app.security import validate_repository_path

# Kept small and explicit rather than inheriting the full parent environment,
# which may carry secrets (AGENTFLOW_SECRET_KEY, DB paths, etc).
_SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TZ", "USER", "SHELL")

DEFAULT_SHUTDOWN_GRACE_SECONDS = 5.0

_TERMINAL_STATUSES = ("COMPLETED", "FAILED", "STOPPED", "TIMED_OUT", "LOST")


@dataclass
class _Handle:
    popen: subprocess.Popen
    pid: int
    requested_status: str | None = None


class HostExecutionProvider(ExecutionProvider):
    """Executes commands directly on the AgentFlow host.

    See docs/EXECUTION_PROVIDER.md sections 4, 7, 8, 10-13, 17, 20, 21.
    """

    def __init__(self, database_path: str, allowed_roots: tuple[str, ...]):
        self._database_path = database_path
        self._allowed_roots = allowed_roots
        self._local = threading.local()
        self._handles: dict[int, _Handle] = {}
        self._lock = threading.Lock()

    def _db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._database_path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            self._local.conn = conn
        return conn

    # -- ExecutionProvider interface -----------------------------------

    def available(self) -> bool:
        return True

    def create_context(self, configuration: dict[str, Any]) -> models.ExecutionContext:
        working_directory = validate_repository_path(
            configuration["working_directory"], self._allowed_roots
        )
        db = self._db()
        context_id = models.create_execution_context(
            db,
            provider="host",
            working_directory=working_directory,
            environment_profile=configuration.get("environment", {}),
            target=configuration.get("target", ""),
        )
        models.set_context_status(db, context_id, "READY")
        self._emit(db, context_id, None, "ExecutionContextCreated")
        return models.get_execution_context(db, context_id)

    def execute(
        self, command: list[str], options: dict[str, Any] | None = None
    ) -> models.Process:
        process = self.start_process(command, options)
        return self.wait(process.id)

    def start_process(
        self, command: list[str], options: dict[str, Any] | None = None
    ) -> models.Process:
        options = options or {}
        context_id = options["context_id"]
        db = self._db()
        context = models.get_execution_context(db, context_id)
        if context is None:
            raise ValueError(f"Unknown execution context {context_id}")

        env = self._build_environment(context.environment_profile, options.get("environment"))
        command_summary = " ".join(command)

        process_id = models.create_process(
            db,
            context_id=context_id,
            provider="host",
            command_summary=command_summary,
            working_directory=context.working_directory,
        )

        try:
            popen = subprocess.Popen(
                command,
                cwd=context.working_directory,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            models.mark_process_terminal(db, process_id, "FAILED", None)
            self._emit(db, context_id, process_id, "ProcessFailed", data=str(exc))
            raise

        with self._lock:
            self._handles[process_id] = _Handle(popen=popen, pid=popen.pid)

        models.mark_process_started(db, process_id, popen.pid)
        self._emit(db, context_id, process_id, "ProcessStarted", data=command_summary)

        timeout = options.get("timeout")
        threading.Thread(
            target=self._run_process,
            args=(process_id, context_id, popen, timeout),
            daemon=True,
        ).start()

        return models.get_process(db, process_id)

    def stop_process(self, process_id: int) -> None:
        with self._lock:
            handle = self._handles.get(process_id)
            if handle is None:
                return
            handle.requested_status = "STOPPED"
            popen = handle.popen
        self._kill_process_group(popen)

    def signal_process(self, process_id: int, sig: int) -> None:
        with self._lock:
            handle = self._handles.get(process_id)
            if handle is None:
                return
            pid = handle.pid
        try:
            os.killpg(os.getpgid(pid), sig)
        except ProcessLookupError:
            pass

    def stream_output(
        self, process_id: int, after_id: int | None = None
    ) -> list[models.ProcessEvent]:
        return models.list_events(self._db(), process_id, after_id=after_id)

    def process_status(self, process_id: int) -> models.Process | None:
        return models.get_process(self._db(), process_id)

    def destroy_context(self, context_id: int) -> None:
        db = self._db()
        for process in models.list_processes_for_context(db, context_id):
            if process.status in ("RUNNING", "STARTING"):
                self.stop_process(process.id)
        models.set_context_status(db, context_id, "DESTROYED")
        self._emit(db, context_id, None, "ExecutionContextDestroyed")

    # -- helpers ----------------------------------------------------------

    def wait(self, process_id: int, poll_interval: float = 0.02) -> models.Process:
        db = self._db()
        while True:
            process = models.get_process(db, process_id)
            if process.status in _TERMINAL_STATUSES:
                return process
            time.sleep(poll_interval)

    def _build_environment(
        self, context_environment: dict[str, str], overrides: dict[str, str] | None
    ) -> dict[str, str]:
        env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
        env.update(context_environment or {})
        env.update(overrides or {})
        return env

    def _emit(
        self,
        db: sqlite3.Connection,
        context_id: int,
        process_id: int | None,
        event_type: str,
        data: str = "",
        stream: str | None = None,
    ) -> int:
        return models.add_event(
            db, context_id, event_type, process_id=process_id, data=data, stream=stream
        )

    def _kill_process_group(
        self, popen: subprocess.Popen, grace: float = DEFAULT_SHUTDOWN_GRACE_SECONDS
    ) -> None:
        # Poll via the Popen object (not os.kill(pid, 0)) so a terminated-but-
        # unreaped zombie is detected as dead instead of spinning the full grace
        # period: os.kill(pid, 0) succeeds for zombies since the pid is still held.
        pid = popen.pid
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if popen.poll() is not None:
                return
            time.sleep(0.05)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _drain_stream(
        self, process_id: int, context_id: int, stream: IO[str], name: str
    ) -> None:
        db = self._db()
        try:
            for line in iter(stream.readline, ""):
                self._emit(
                    db, context_id, process_id, "ProcessOutput", data=line.rstrip("\n"), stream=name
                )
        finally:
            stream.close()

    def _run_process(
        self,
        process_id: int,
        context_id: int,
        popen: subprocess.Popen,
        timeout: float | None,
    ) -> None:
        db = self._db()
        stdout_thread = threading.Thread(
            target=self._drain_stream,
            args=(process_id, context_id, popen.stdout, "stdout"),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._drain_stream,
            args=(process_id, context_id, popen.stderr, "stderr"),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        try:
            popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            with self._lock:
                handle = self._handles.get(process_id)
                if handle is not None:
                    handle.requested_status = "TIMED_OUT"
            self._kill_process_group(popen)
            popen.wait()

        stdout_thread.join()
        stderr_thread.join()

        with self._lock:
            handle = self._handles.pop(process_id, None)
        requested_status = handle.requested_status if handle else None
        status = requested_status if requested_status else "COMPLETED"

        models.mark_process_terminal(db, process_id, status, popen.returncode)
        event_type = {
            "TIMED_OUT": "ProcessTimedOut",
            "STOPPED": "ProcessStopped",
        }.get(status, "ProcessCompleted")
        self._emit(db, context_id, process_id, event_type, data=str(popen.returncode))
