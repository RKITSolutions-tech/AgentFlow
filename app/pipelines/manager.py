"""Runs executions on background threads, one worker per execution."""
from __future__ import annotations

import sqlite3
import threading

from app.execution.host import HostExecutionProvider
from app.pipelines import executions
from app.pipelines.engine import PipelineEngine
from app.projects import lock
from app.runs import artifacts
from app.runs.security import redact


def default_agent_factory(config, provider):
    """Adapter for AGENT elements: the fake agent under `PLANNING_AGENT=fake`
    (deterministic CI), Claude under `PLANNING_AGENT=claude`, a `local`
    catalog model under `PLANNING_AGENT=local` (validated via
    `PLANNING_MODEL`, driven by whichever real CLI `PLANNING_LOCAL_ADAPTER`
    names), otherwise Codex through `provider`.

    Shares its config knobs with the sprint planning agent
    (app/sprints/planning_agent.py._local_planning_adapter) since both pick a
    real adapter type the same way. `_h_agent` (app/pipelines/engine.py) is
    what actually makes `local` do anything: it threads a resolved `local`
    catalog model onto the `AgentContext` a step names in its own
    `config["model"]`, which is what engages the adapter's own
    `local`-catalog handling (both keyed off `context.model`) — this factory
    only has to make sure a local-capable adapter class (Codex or Claude,
    both understand `context.model`) is the one driving the session.
    """

    def build(db):
        kind = str(config.get("PLANNING_AGENT", "codex")).lower()
        if kind == "fake":
            from app.agents.fake import FakeAgentAdapter

            return FakeAgentAdapter(db)
        if kind == "claude":
            from app.agents.claude import ClaudeAdapter

            return ClaudeAdapter(db=db, execution_provider=provider)
        if kind == "local":
            from app.settings.models import resolve_local_model

            # Validate early, exactly like PLANNING_AGENT=local
            # (app/sprints/planning_agent.py._local_planning_adapter): a
            # misconfigured PLANNING_MODEL fails loudly here rather than
            # quietly starting a session with no model override applied.
            resolve_local_model(
                db, config.get("PLANNING_MODEL", ""), label="AGENTFLOW_PLANNING_MODEL"
            )
            if str(config.get("PLANNING_LOCAL_ADAPTER", "codex")).lower() == "claude":
                from app.agents.claude import ClaudeAdapter

                return ClaudeAdapter(db=db, execution_provider=provider)
            from app.agents.codex import CodexAdapter

            return CodexAdapter(db=db, execution_provider=provider)
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
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _engine(self, db: sqlite3.Connection) -> PipelineEngine:
        return PipelineEngine(
            db, self.provider, self._root, self._agent_factory, extra_patterns=self._patterns
        )

    def start(self, execution_id: int, wait: bool = False) -> bool:
        """Run or resume an execution in the background. A busy project raises
        LockConflict, or with `wait` queues the execution behind it (returns True)."""
        with self._lock:
            if execution_id in self._threads:
                raise ValueError(f"Execution {execution_id} is already running")
        heartbeat, ticket = self._take_lock(execution_id, wait)
        with self._lock:
            thread = threading.Thread(
                target=self._work, args=(execution_id, heartbeat, ticket), daemon=True, name=f"pipeline-{execution_id}"
            )
            self._threads[execution_id] = thread
        thread.start()
        return ticket is not None

    def _take_lock(self, execution_id: int, wait: bool = False):
        """Own the project (or the execution's repository) while it runs; a rival's
        lock stops a never-started execution (a resumed one stays paused) unless
        `wait` queues it. Returns (heartbeat, ticket)."""
        db = self._connect()
        try:
            ex = executions.get_execution(db, execution_id)
            try:
                return lock.begin(
                    db, self._database_path, ex.project_id, "pipeline_run", execution_id,
                    repository_id=lock.scope_for(db, ex.project_id, ex.repository_id), wait=wait,
                )
            except lock.LockConflict:
                if ex.status == "PENDING":
                    executions.update_execution(db, execution_id, status="CANCELLED")
                raise
        finally:
            db.close()

    def _work(self, execution_id: int, heartbeat: lock.Heartbeat | None = None, ticket: lock.Ticket | None = None) -> None:
        db = self._connect()
        try:
            if ticket is not None:
                try:
                    heartbeat = ticket.wait(should_stop=lambda: executions.get_execution(db, execution_id).cancel_requested)
                except lock.LockConflict as exc:
                    ex = executions.get_execution(db, execution_id)
                    if ex.status == "PENDING":  # never started: it does not run, and says why
                        executions.update_execution(db, execution_id, status="CANCELLED", reason=str(exc))
                    return
            engine = self._engine(db)
            with self._lock:
                self._engines[execution_id] = engine
            engine.run(execution_id)
        finally:
            if heartbeat is not None:
                heartbeat.stop()
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
