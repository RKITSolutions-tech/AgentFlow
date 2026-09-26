from __future__ import annotations

import logging
import os
import shlex
import signal
import sqlite3
import threading
import time
from dataclasses import dataclass

from app.execution import models as exec_models
from app.execution.host import HostExecutionProvider
from app.projects import lock
from app.projects import models as project_models
from app.runs import artifacts, models
from app.runs.security import REDACTION_MARK, redact

logger = logging.getLogger(__name__)

REPLY_TAIL_CHARS = 8000
_PROCESS_TO_STEP = {
    "COMPLETED": None,  # decided by exit code
    "TIMED_OUT": "TIMED_OUT",
    "STOPPED": "CANCELLED",
    "FAILED": "FAILED",
    "LOST": "BLOCKED",
}


@dataclass
class _Active:
    stop_requested: bool = False
    paused: bool = False
    process_id: int | None = None
    # seq -> unredacted command, held in memory only (never persisted).
    live_commands: dict[int, str] | None = None
    heartbeat: object | None = None
    ticket: object | None = None  # a place in the lock queue, when started with wait=True


def pid_alive(pid: int | None) -> bool:
    """True if `pid` is a live (non-zombie) process."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


class RunManager:
    """Executes Runs step by step through the ExecutionProvider.

    One instance lives on the Flask app (`app.extensions["run_manager"]`) so
    pause/stop requests from later HTTP requests reach the worker thread that
    owns the process. All durable state is in SQLite, so `reconcile()` can
    rebuild the picture after a restart.
    """

    def __init__(self, config):
        self._database_path = config["DATABASE_PATH"]
        self._extra_patterns = tuple(config.get("REDACT_PATTERNS", ()))
        self._artifact_root = artifacts.artifact_root(config)
        self.provider = HostExecutionProvider(
            self._database_path,
            config["ALLOWED_PROJECT_ROOTS"],
            redactor=lambda text: redact(text, self._extra_patterns)[0],
        )
        self._active: dict[int, _Active] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._lock = threading.Lock()

    @property
    def artifact_root(self) -> str:
        return self._artifact_root

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._database_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # -- control ---------------------------------------------------------

    def start(self, run_id: int, live_commands: dict[int, str] | None = None, wait: bool = False) -> bool:
        """Run in the background. A busy project fails the Run (LockConflict), or with
        `wait` queues it: the worker starts when the project is free."""
        with self._lock:
            if run_id in self._active:
                raise ValueError(f"Run {run_id} is already executing")
            heartbeat, ticket = self._take_lock(run_id, wait)
            self._active[run_id] = _Active(live_commands=live_commands, heartbeat=heartbeat, ticket=ticket)
            thread = threading.Thread(
                target=self._execute, args=(run_id,), daemon=True, name=f"run-{run_id}"
            )
            self._threads[run_id] = thread
        thread.start()
        return ticket is not None

    def _take_lock(self, run_id: int, wait: bool = False):
        """Own the project (or its repository) while the Run executes. A Run that
        cannot get it is failed (it never started) and LockConflict reaches the
        caller, unless `wait` queues it. Returns (heartbeat, ticket)."""
        db = self._connect()
        try:
            run = models.get_run(db, run_id)
            try:
                return lock.begin(
                    db, self._database_path, run.project_id, "manual_run", run_id,
                    repository_id=lock.scope_for(db, run.project_id, run.repository_id), wait=wait,
                )
            except lock.LockConflict as exc:
                if run.status == "CREATED":
                    models.set_run_status(db, run_id, "FAILED", str(exc))
                raise
        finally:
            db.close()

    def join(self, run_id: int, timeout: float | None = None) -> bool:
        thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def is_executing(self, run_id: int) -> bool:
        return run_id in self._active

    def pause(self, run_id: int) -> None:
        state = self._require_active(run_id)
        pid = self._live_pid(state)
        db = self._connect()
        try:
            run = models.get_run(db, run_id, with_steps=False)
            if run.status != "RUNNING":
                raise ValueError(f"Run is {run.status}, only a RUNNING run can be paused")
            if pid is not None:
                self.provider.signal_process(state.process_id, signal.SIGSTOP)
            state.paused = True
            models.set_run_status(db, run_id, "PAUSED")
            models.add_event(db, run_id, "RunPaused")
        finally:
            db.close()

    def resume(self, run_id: int) -> None:
        state = self._require_active(run_id)
        db = self._connect()
        try:
            run = models.get_run(db, run_id, with_steps=False)
            if run.status != "PAUSED":
                raise ValueError(f"Run is {run.status}, only a PAUSED run can be resumed")
            if state.process_id is not None:
                self.provider.signal_process(state.process_id, signal.SIGCONT)
            state.paused = False
            models.set_run_status(db, run_id, "RUNNING")
            models.add_event(db, run_id, "RunResumed")
        finally:
            db.close()

    def stop(self, run_id: int) -> None:
        state = self._require_active(run_id)
        state.stop_requested = True
        if state.process_id is not None:
            # A stopped (SIGSTOP) group cannot act on SIGTERM until continued.
            self.provider.signal_process(state.process_id, signal.SIGCONT)
            self.provider.stop_process(state.process_id)

    def _require_active(self, run_id: int) -> _Active:
        state = self._active.get(run_id)
        if state is None:
            raise ValueError("Run is not executing in this AgentFlow process")
        return state

    def _live_pid(self, state: _Active) -> int | None:
        if state.process_id is None:
            return None
        process = self.provider.process_status(state.process_id)
        return process.external_process_id if process else None

    # -- execution -------------------------------------------------------

    def _execute(self, run_id: int) -> None:
        db = self._connect()
        state = self._active[run_id]
        context_id = None
        try:
            if state.ticket is not None:
                try:
                    state.heartbeat = state.ticket.wait(should_stop=lambda: state.stop_requested)
                except lock.LockConflict as exc:
                    if state.stop_requested:
                        models.set_run_status(db, run_id, "CANCELLED", "Stopped while waiting for the project")
                        models.add_event(db, run_id, "RunCancelled", data="Stopped while waiting for the project")
                    else:
                        self._fail_run(db, run_id, str(exc))
                    return
            run = models.get_run(db, run_id)
            repo = project_models.get_repository(db, run.project_id, run.repository_id)
            if repo is None:
                self._fail_run(db, run_id, "Repository no longer exists")
                return
            try:
                context = self.provider.create_context({"working_directory": repo.path})
            except ValueError as exc:
                self._fail_run(db, run_id, str(exc))
                return
            context_id = context.id
            models.set_run_status(db, run_id, "RUNNING")
            models.add_event(db, run_id, "RunStarted")

            final, reason = "COMPLETED", ""
            for step in run.steps:
                if state.stop_requested:
                    final, reason = "CANCELLED", "Stopped by user"
                    self._skip_remaining(db, run_id, step.seq)
                    break
                outcome = self._run_step(db, run, step, context, state)
                if outcome != "PASSED":
                    final = {"CANCELLED": "CANCELLED", "TIMED_OUT": "TIMED_OUT"}.get(
                        outcome, "FAILED"
                    )
                    reason = {
                        "CANCELLED": "Stopped by user",
                        "TIMED_OUT": f"Step {step.seq} timed out",
                    }.get(outcome, f"Step {step.seq} failed")
                    self._skip_remaining(db, run_id, step.seq + 1)
                    break
            models.set_run_status(db, run_id, final, reason)
            models.add_event(db, run_id, f"Run{final.title().replace('_', '')}", data=reason)
        except Exception as exc:  # never leave a Run stuck RUNNING
            logger.exception("Run %s crashed", run_id)
            self._fail_run(db, run_id, f"Internal error: {exc}")
        finally:
            if context_id is not None:
                self.provider.destroy_context(context_id)
            if state.heartbeat is not None:
                state.heartbeat.stop()
            with self._lock:
                self._active.pop(run_id, None)
            db.close()

    def _fail_run(self, db: sqlite3.Connection, run_id: int, reason: str) -> None:
        models.set_run_status(db, run_id, "FAILED", reason)
        models.add_event(db, run_id, "RunFailed", data=reason)

    def _skip_remaining(self, db: sqlite3.Connection, run_id: int, from_seq: int) -> None:
        db.execute(
            "UPDATE run_steps SET status = 'SKIPPED' "
            "WHERE run_id = ? AND seq >= ? AND status = 'PENDING'",
            (run_id, from_seq),
        )
        db.commit()

    def _run_step(self, db, run: models.Run, step: models.Step, context, state: _Active) -> str:
        models.mark_step_started(db, step.id)
        models.add_event(db, run.id, "StepStarted", step_id=step.id, data=step.name)
        try:
            command = shlex.split((state.live_commands or {}).get(step.seq, step.command))
            if not command:
                raise ValueError("empty command")
            process = self.provider.start_process(
                command,
                {
                    "context_id": context.id,
                    "timeout": step.timeout_seconds,
                    "command_summary": step.command,  # already redacted
                },
            )
        except (ValueError, OSError) as exc:
            message, _ = redact(f"Could not start: {exc}", self._extra_patterns)
            models.finish_step(db, step.id, "FAILED", reply=message)
            models.add_event(db, run.id, "StepFailed", step_id=step.id, data=message)
            return "FAILED"

        state.process_id = process.id
        models.set_step_process(db, step.id, process.id)
        if state.stop_requested:  # stop() raced with the process starting
            self.provider.stop_process(process.id)
        finished = self.provider.wait(process.id)
        state.process_id = None

        events = self.provider.stream_output(process.id)
        raw_log = "\n".join(
            (f"[stderr] {e.data}" if e.stream == "stderr" else e.data)
            for e in events
            if e.event_type == "ProcessOutput"
        )
        # Events arrive already line-redacted; redact again for anything that
        # only matches across lines, and note redaction from either pass.
        log, was_redacted = redact(raw_log, self._extra_patterns)
        was_redacted = was_redacted or REDACTION_MARK in log
        artifacts.write_artifact(
            db, self._artifact_root, run.id, step, "log", "output.log", log + "\n",
            redacted=was_redacted,
        )
        artifacts.collect_files(
            db, self._artifact_root, run.id, step, context.working_directory,
            self._extra_patterns,
        )

        status = _PROCESS_TO_STEP.get(finished.status)
        if status is None:
            status = "PASSED" if finished.exit_code == 0 else "FAILED"
        models.finish_step(
            db, step.id, status, exit_code=finished.exit_code, reply=log[-REPLY_TAIL_CHARS:]
        )
        models.add_event(
            db,
            run.id,
            f"Step{status.title().replace('_', '')}",
            step_id=step.id,
            data=f"exit code {finished.exit_code}",
        )
        return status

    # -- restart reconciliation -------------------------------------------

    def reconcile(self) -> list[int]:
        """Resolve Runs left RUNNING/PAUSED by a previous AgentFlow process.

        A Run whose process is gone becomes BLOCKED. A process that is still
        alive cannot be re-attached (its output pipes died with us), so a
        watcher blocks the Run once it exits.
        """
        db = self._connect()
        blocked: list[int] = []
        try:
            for run in models.list_runs_by_status(db, ("RUNNING", "PAUSED")):
                if run.id in self._active:
                    continue
                step = next((s for s in run.steps if s.status == "RUNNING"), None)
                process = exec_models.get_process(db, step.process_id) if step and step.process_id else None
                pid = process.external_process_id if process else None
                if pid_alive(pid):
                    threading.Thread(
                        target=self._watch_orphan, args=(run.id, step.id, process.id, pid), daemon=True
                    ).start()
                    continue
                self._block(db, run.id, step, process, "Process missing after restart")
                blocked.append(run.id)
        finally:
            db.close()
        return blocked

    def _watch_orphan(self, run_id: int, step_id: int, process_id: int, pid: int) -> None:
        while pid_alive(pid):
            time.sleep(1.0)
        db = self._connect()
        try:
            step = models.get_step(db, step_id)
            process = exec_models.get_process(db, process_id)
            self._block(
                db, run_id, step, process,
                "Process ended while AgentFlow was not watching; result unknown",
            )
        finally:
            db.close()

    def _block(self, db, run_id: int, step, process, reason: str) -> None:
        if process is not None:
            exec_models.mark_process_terminal(db, process.id, "LOST", None)
        if step is not None:
            models.finish_step(db, step.id, "BLOCKED")
            self._skip_remaining(db, run_id, step.seq + 1)
        models.set_run_status(db, run_id, "BLOCKED", reason)
        models.add_event(db, run_id, "RunBlocked", step_id=step.id if step else None, data=reason)
