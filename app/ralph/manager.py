"""Runs Ralph on background threads. Pause, cancel and steering are database
flags, so they can be set from any request without touching the worker."""
from __future__ import annotations

import sqlite3
import threading

from app.pipelines.engine import PipelineEngine
from app.pipelines.manager import PipelineManager
from app.projects import lock
from app.ralph import models
from app.ralph.orchestrator import RalphOrchestrator
from app.sprints import queue


class RalphManager:
    def __init__(self, pipelines: PipelineManager):
        self._pipelines = pipelines
        self._threads: dict[int, threading.Thread] = {}
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        return self._pipelines._connect()

    def orchestrator(self, db: sqlite3.Connection) -> RalphOrchestrator:
        p = self._pipelines
        engine = PipelineEngine(db, p.provider, p._root, p._agent_factory, extra_patterns=p._patterns)
        return RalphOrchestrator(db, p.provider, engine, p._agent_factory, p._patterns)

    def start(self, run_id: int, wait: bool = False) -> bool:
        """Run or resume a run in the background. A busy project raises LockConflict
        (a ValueError), or with `wait` queues the run behind it."""
        with self._lock:
            if run_id in self._threads:
                raise ValueError(f"Ralph run {run_id} is already executing")
        db = self._connect()
        try:
            run = models.get_run(db, run_id)
            # Raises LockConflict (a ValueError) if another run owns the project.
            heartbeat, ticket = lock.begin(
                db, self._pipelines._database_path, run.project_id, "ralph_run", run_id,
                repository_id=lock.scope_for(db, run.project_id, run.repository_id), wait=wait,
            )
        finally:
            db.close()
        with self._lock:
            thread = threading.Thread(target=self._work, args=(run_id, heartbeat, ticket), daemon=True, name=f"ralph-{run_id}")
            self._threads[run_id] = thread
        thread.start()
        return ticket is not None

    def _work(self, run_id: int, heartbeat: lock.Heartbeat | None = None, ticket: lock.Ticket | None = None) -> None:
        db = self._connect()
        try:
            if ticket is not None:
                try:
                    heartbeat = ticket.wait(should_stop=lambda: bool(models.get_run(db, run_id).cancel_requested))
                except lock.LockConflict as exc:
                    self._gave_up_waiting(db, run_id, str(exc))
                    return
            self.orchestrator(db).run(run_id)
        finally:
            if heartbeat is not None:
                heartbeat.stop()
            with self._lock:
                self._threads.pop(run_id, None)
            db.close()
        self._advance_sprint(run_id)

    def _gave_up_waiting(self, db: sqlite3.Connection, run_id: int, reason: str) -> None:
        """A queued run never got the project: cancelled if a person stopped it,
        otherwise left CREATED (resumable) with the reason and needing attention."""
        run = models.get_run(db, run_id)
        if run.cancel_requested:
            models.update_run(db, run_id, status="CANCELLED", reason="Stopped while waiting for the project", completed_at=_now())
        else:
            models.update_run(db, run_id, reason=reason, needs_attention=1)
        queue.sync_from_run(db, run_id)

    def _advance_sprint(self, run_id: int) -> None:
        """Automatic sprint mode: hand the project to the next eligible task."""
        db = self._connect()
        try:
            run = models.get_run(db, run_id)
            if run is None or not run.sprint_id or run.status not in queue.ADVANCE_AFTER:
                return
            promoted = queue.advance(db, run.sprint_id)
        except Exception:  # a scheduling problem must never crash the finished worker
            return
        finally:
            db.close()
        if promoted:
            try:
                self.start(promoted[1])
            except ValueError:
                pass  # lock taken meanwhile: the run stays CREATED and can be resumed

    def is_executing(self, run_id: int) -> bool:
        return run_id in self._threads

    def join(self, run_id: int, timeout: float | None = None) -> bool:
        thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def _flag(self, action: str, run_id: int, *args):
        db = self._connect()
        try:
            return getattr(self.orchestrator(db), action)(run_id, *args)
        finally:
            db.close()

    def pause(self, run_id: int) -> None:
        self._flag("pause", run_id)

    def cancel(self, run_id: int) -> None:
        """Flag the run; a run with no worker (paused/blocked) is closed directly."""
        self._flag("cancel", run_id)
        if not self.is_executing(run_id):
            db = self._connect()
            try:
                run = models.get_run(db, run_id)
                if run.status not in models.RUN_TERMINAL:
                    models.update_run(db, run_id, status="CANCELLED", reason="Stopped by user", completed_at=_now())
                    queue.sync_from_run(db, run_id)
            finally:
                db.close()

    def steer(self, run_id: int, message: str) -> int:
        return self._flag("steer", run_id, message)

    def finalize(self, run_id: int):
        return self._flag("finalize", run_id)

    def unblock(self, run_id: int, message: str = "") -> None:
        self._flag("unblock", run_id, message)
        self.start(run_id)

    def reconcile(self) -> list[int]:
        """After a restart, a run left RUNNING/VERIFYING has no worker: mark it
        BLOCKED so a person decides whether to continue."""
        db = self._connect()
        try:
            rows = db.execute("SELECT id FROM ralph_runs WHERE status IN ('RUNNING', 'VERIFYING')").fetchall()
            for (run_id,) in rows:
                models.update_run(
                    db, run_id, status="BLOCKED", needs_attention=1,
                    reason="AgentFlow restarted while this run was executing",
                )
                queue.sync_from_run(db, run_id)  # the task goes BLOCKED; the sprint can move on
            return [r[0] for r in rows]
        finally:
            db.close()

    def resume_automatic_sprints(self) -> list[int]:
        """After a restart, give every EXECUTING sprint in automatic mode its next
        eligible task (its in-flight run was BLOCKED by `reconcile`). Returns the
        started run ids; a sprint with nothing eligible is left waiting."""
        db = self._connect()
        try:
            ids = [r[0] for r in db.execute("SELECT id FROM sprints WHERE auto_run = 1 AND status = 'EXECUTING'")]
            promoted = [p for p in (queue.advance(db, sid) for sid in ids) if p]
        finally:
            db.close()
        started = []
        for _, run_id in promoted:
            try:
                self.start(run_id)
                started.append(run_id)
            except ValueError:
                pass  # lock taken meanwhile: the run stays CREATED and can be resumed
        return started


def _now() -> str:
    from app.runs.models import now

    return now()
