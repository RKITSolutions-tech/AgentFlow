"""Runs Ralph on background threads. Pause, cancel and steering are database
flags, so they can be set from any request without touching the worker."""
from __future__ import annotations

import sqlite3
import threading

from app.pipelines.engine import PipelineEngine
from app.pipelines.manager import PipelineManager
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

    def start(self, run_id: int) -> None:
        """Run or resume a run in the background."""
        with self._lock:
            if run_id in self._threads:
                raise ValueError(f"Ralph run {run_id} is already executing")
            thread = threading.Thread(target=self._work, args=(run_id,), daemon=True, name=f"ralph-{run_id}")
            self._threads[run_id] = thread
        thread.start()

    def _work(self, run_id: int) -> None:
        db = self._connect()
        try:
            self.orchestrator(db).run(run_id)
        finally:
            with self._lock:
                self._threads.pop(run_id, None)
            db.close()

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
            return [r[0] for r in rows]
        finally:
            db.close()


def _now() -> str:
    from app.runs.models import now

    return now()
