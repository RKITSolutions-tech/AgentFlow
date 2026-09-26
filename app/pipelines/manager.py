"""Runs executions on background threads, one worker per execution."""
from __future__ import annotations

import sqlite3
import threading

from app.execution.host import HostExecutionProvider
from app.pipelines import executions
from app.pipelines.engine import PipelineEngine
from app.runs import artifacts
from app.runs.security import redact


def default_agent_factory(config, provider):
    """Adapter for AGENT elements: the fake agent under `PLANNING_AGENT=fake`
    (deterministic CI), otherwise Codex through `provider`."""

    def build(db):
        if str(config.get("PLANNING_AGENT", "codex")).lower() == "fake":
            from app.agents.fake import FakeAgentAdapter

            return FakeAgentAdapter(db)
        from app.agents.codex import CodexAdapter

        return CodexAdapter(db=db, execution_provider=provider)

    return build


class PipelineManager:
    """One instance lives on the app (`app.extensions["pipeline_manager"]`) so
    cancel requests from later HTTP requests reach the worker that owns the
    process. Durable state is in SQLite; a paused execution has no thread."""

    def __init__(self, config, agent_factory=None, provider=None):
        self._database_path = config["DATABASE_PATH"]
        self._patterns = tuple(config.get("REDACT_PATTERNS", ()))
        self._root = artifacts.artifact_root(config)
        self.provider = provider or HostExecutionProvider(
            self._database_path,
            config["ALLOWED_PROJECT_ROOTS"],
            redactor=lambda text: redact(text, self._patterns)[0],
        )
        self._agent_factory = agent_factory or default_agent_factory(config, self.provider)
        self._threads: dict[int, threading.Thread] = {}
        self._engines: dict[int, PipelineEngine] = {}
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._database_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _engine(self, db: sqlite3.Connection) -> PipelineEngine:
        return PipelineEngine(
            db, self.provider, self._root, self._agent_factory, extra_patterns=self._patterns
        )

    def start(self, execution_id: int) -> None:
        """Run or resume an execution in the background."""
        with self._lock:
            if execution_id in self._threads:
                raise ValueError(f"Execution {execution_id} is already running")
            thread = threading.Thread(
                target=self._work, args=(execution_id,), daemon=True, name=f"pipeline-{execution_id}"
            )
            self._threads[execution_id] = thread
        thread.start()

    def _work(self, execution_id: int) -> None:
        db = self._connect()
        try:
            engine = self._engine(db)
            with self._lock:
                self._engines[execution_id] = engine
            engine.run(execution_id)
        finally:
            with self._lock:
                self._threads.pop(execution_id, None)
                self._engines.pop(execution_id, None)
            db.close()

    def is_running(self, execution_id: int) -> bool:
        return execution_id in self._threads

    def join(self, execution_id: int, timeout: float | None = None) -> bool:
        thread = self._threads.get(execution_id)
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def cancel(self, execution_id: int) -> None:
        """Stop a running execution, or finish a paused one (its teardown runs).

        The flag is set on a fresh connection (a worker's connection belongs to
        its own thread); the worker notices it between steps and the process it
        is waiting on is stopped straight away.
        """
        db = self._connect()
        try:
            self._engine(db).cancel(execution_id)
            worker = self._engines.get(execution_id)
            pid = worker.active_process(execution_id) if worker else None
            if pid is not None:
                self.provider.stop_process(pid)
            ex = executions.get_execution(db, execution_id)
            finalise = ex.status == "PAUSED" and execution_id not in self._threads
        finally:
            db.close()
        if finalise:
            self.start(execution_id)

    def resolve_manual(self, step_id: int, decision: str, by: str, comment: str = "", value=None) -> None:
        """Record a decision, then continue the execution in the background."""
        db = self._connect()
        try:
            engine = self._engine(db)
            engine.resolve_manual(step_id, decision, by, comment, value)
            execution_id = executions.get_step(db, step_id).execution_id
        finally:
            db.close()
        self.start(execution_id)
