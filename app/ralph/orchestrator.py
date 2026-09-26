"""Ralph: iterate agent work + verification until a Task genuinely passes
(docs/RUN_AND_RALPH.md §4-§16).

One `run()` call executes iterations until the run finishes, pauses or is
blocked. Everything durable is in SQLite (iteration number, steering, flags),
so pause/resume/cancel are just flags checked at iteration boundaries and a
resumed run rebuilds itself from the database.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from typing import Callable

from app.agents.base import AgentContext
from app.acceptance import service as acceptance
from app.artifacts import collector
from app.pipelines import executions
from app.pipelines.engine import PipelineEngine
from app.projects import models as project_models
from app.ralph import models
from app.runs.models import now
from app.runs.security import redact

STANDARD_INSTRUCTIONS = (
    "Work only on the task below and keep changes minimal.",
    "Extend existing code rather than replacing it.",
    "Do not commit; AgentFlow commits after verification passes.",
    "Stop when you believe the acceptance criteria are met; verification decides.",
)
MAX_SNAPSHOT_FILE_BYTES = 1_000_000
FEEDBACK_CHARS = 1500
_NOISE = re.compile(r"\d+(\.\d+)?|0x[0-9a-f]+|/tmp/\S+|[0-9a-f]{7,40}", re.I)


class RalphOrchestrator:
    def __init__(
        self,
        db: sqlite3.Connection,
        provider,
        engine: PipelineEngine,
        agent_factory: Callable[[sqlite3.Connection], object],
        extra_patterns: tuple[str, ...] = (),
        clock: Callable[[], float] = time.monotonic,
    ):
        self._db = db
        self._provider = provider
        self._engine = engine
        self._agent_factory = agent_factory
        self._patterns = extra_patterns
        self._clock = clock

    # -- controls (flags; safe to set from any connection) -----------------------------

    def pause(self, run_id: int) -> None:
        run = self._require(run_id)
        if run.status not in ("RUNNING", "VERIFYING", "CREATED"):
            raise ValueError(f"A {run.status} run cannot be paused")
        models.update_run(self._db, run_id, pause_requested=1)

    def cancel(self, run_id: int) -> None:
        run = self._require(run_id)
        if run.status in models.RUN_TERMINAL:
            raise ValueError(f"Run is already {run.status.lower()}")
        models.update_run(self._db, run_id, cancel_requested=1)

    def steer(self, run_id: int, message: str) -> int:
        run = self._require(run_id)
        if run.status in models.RUN_TERMINAL:
            raise ValueError("Steering a finished run has no effect")
        return models.add_steering(self._db, run_id, message, run.current_iteration)

    def unblock(self, run_id: int, message: str = "") -> None:
        """Let a BLOCKED (no progress) or PAUSED run continue, optionally with steering."""
        run = self._require(run_id)
        if run.status not in ("BLOCKED", "PAUSED", "WAITING_FOR_HUMAN"):
            raise ValueError(f"A {run.status} run cannot be resumed")
        if message.strip():
            models.add_steering(self._db, run_id, message, run.current_iteration)
        models.update_run(
            self._db, run_id, pause_requested=0, needs_attention=0, awaiting_acceptance=0,
            status="CREATED", reason="",
        )

    # -- main loop ------------------------------------------------------------------------

    def run(self, run_id: int) -> models.RalphRun:
        db = self._db
        run = self._require(run_id)
        if run.status in models.RUN_TERMINAL:
            return run
        if run.status in ("BLOCKED", "WAITING_FOR_HUMAN"):
            raise ValueError("Resolve the block (unblock) before running again")
        first = run.started_at is None
        models.update_run(
            db, run_id, status="RUNNING", pause_requested=0,
            **({"started_at": now()} if first else {}),
        )
        started = self._clock()
        context_id = None
        try:
            context = self._provider.create_context(
                {"working_directory": self._workdir(run)}
            )
            context_id = context.id
            while True:
                run = self._require(run_id)
                spent = run.elapsed_seconds + (self._clock() - started)
                if run.cancel_requested:
                    return self._end(run_id, "CANCELLED", "Stopped by user", started)
                if run.pause_requested:
                    return self._end(run_id, "PAUSED", "Paused at an iteration boundary", started)
                if run.max_runtime_seconds is not None and spent >= run.max_runtime_seconds:
                    return self._end(run_id, "TIMED_OUT", f"Exceeded {run.max_runtime_seconds:g}s", started)
                if run.current_iteration >= run.max_iterations:
                    return self._end(
                        run_id, "FAILED",
                        f"Not passing after {run.max_iterations} iteration(s)", started,
                    )
                outcome, detail = self._iterate(run, context_id)
                if outcome == "PASSED":
                    return self._complete_or_wait(run_id, context_id, started)
                if outcome == "BLOCKED":
                    models.update_run(db, run_id, needs_attention=1)
                    return self._end(run_id, "BLOCKED", detail, started)
                if outcome == "WAITING":
                    return self._end(run_id, "WAITING_FOR_HUMAN", detail, started)
                if outcome == "FATAL":
                    return self._end(run_id, "FAILED", detail, started)
        except Exception as exc:  # never leave a run stuck RUNNING
            return self._end(run_id, "FAILED", f"Internal error: {exc}", started)
        finally:
            if context_id is not None:
                try:
                    self._provider.destroy_context(context_id)
                except Exception:
                    pass

    def _end(self, run_id: int, status: str, reason: str, started: float) -> models.RalphRun:
        run = self._require(run_id)
        fields = {
            "status": status,
            "reason": reason,
            "elapsed_seconds": run.elapsed_seconds + (self._clock() - started),
        }
        if status in models.RUN_TERMINAL:
            fields["completed_at"] = now()
        models.update_run(self._db, run_id, **fields)
        return self._require(run_id)

    # -- one iteration ----------------------------------------------------------------------

    def _iterate(self, run: models.RalphRun, context_id: int) -> tuple[str, str]:
        """Returns (PASSED | CONTINUE | BLOCKED | WAITING | FATAL, detail)."""
        db = self._db
        number = run.current_iteration + 1
        previous = models.list_iterations(db, run.id)
        last = previous[-1] if previous else None
        steering = models.unconsumed_steering(db, run.id)

        prompt = self._build_prompt(run, number, last, steering)
        clean_prompt, redacted = redact(prompt, self._patterns)
        iteration_id = models.add_iteration(db, run.id, number, clean_prompt, redacted)
        models.update_run(db, run.id, current_iteration=number)
        models.consume_steering(db, [s.id for s in steering], number)

        # 1. agent work (same session after the first iteration)
        reply, agent_error = self._invoke_agent(run, context_id, prompt)
        clean_reply, reply_redacted = redact(reply, self._patterns)
        models.update_iteration(
            db, iteration_id, reply=clean_reply, redacted=redacted or reply_redacted,
            status="COLLECTING_CHANGES",
        )
        if self._flag(run.id, "cancel_requested"):
            models.update_iteration(db, iteration_id, status="CANCELLED", completed_at=now())
            return "CONTINUE", ""  # the loop's cancel check ends the run

        # 2. collect changes
        files, change_sig = self._snapshot(context_id)
        models.update_iteration(db, iteration_id, changed_files=files, change_signature=change_sig)
        self._store_diff(run, number, context_id, files)

        # 3. verify through the pipeline engine
        models.update_iteration(db, iteration_id, status="VERIFYING")
        models.update_run(db, run.id, status="VERIFYING")
        execution_id = self._engine.create(
            run.verification_pipeline, run.project_id, run.repository_id, run.sprint_id,
            variables={"task": run.task_text, "iteration": number, "run": run.id},
        )
        execution = self._engine.run(execution_id)
        models.update_run(db, run.id, status="RUNNING")
        models.update_iteration(db, iteration_id, verification_execution_id=execution_id, status="EVALUATING")

        if execution.status == "PAUSED":
            models.update_iteration(db, iteration_id, status="BLOCKED", analysis="Verification is waiting for a person")
            return "WAITING", f"Verification pipeline is waiting for a decision (execution {execution_id})"
        if execution.status == "COMPLETED" and agent_error is None:
            models.update_iteration(
                db, iteration_id, status="PASSED", analysis="Verification passed", next_action="commit",
                completed_at=now(),
            )
            return "PASSED", ""
        if execution.status == "CANCELLED":
            models.update_iteration(db, iteration_id, status="CANCELLED", completed_at=now())
            return "CONTINUE", ""

        # 4. failure analysis
        evidence, signature = self._analyse_failure(execution_id, agent_error)
        models.update_iteration(
            db, iteration_id, status="FAILED", failure_signature=signature, analysis=evidence,
            completed_at=now(),
        )
        verdict = self._no_progress(run, iteration_id)
        if verdict:
            models.update_iteration(db, iteration_id, status="NO_PROGRESS", next_action="manual_review", analysis=f"{evidence}\n\n{verdict}")
            return "BLOCKED", verdict
        models.update_iteration(db, iteration_id, next_action="retry_with_feedback")
        return "CONTINUE", ""

    def _build_prompt(self, run, number: int, last: models.Iteration | None, steering) -> str:
        parts = [f"Task: {run.title}", run.task_text.strip()]
        if run.acceptance:
            parts.append("Acceptance criteria:\n" + "\n".join(f"- {c}" for c in run.acceptance))
        parts.append("Instructions:\n" + "\n".join(f"- {i}" for i in STANDARD_INSTRUCTIONS))
        if steering:
            parts.append("Steering from the user (follow these):\n" + "\n".join(f"- {s.message}" for s in steering))
        if last is not None and last.status in ("FAILED", "NO_PROGRESS"):
            attempts = models.list_iterations(self._db, run.id)
            history = "\n".join(
                f"- Iteration {i.number}: {i.status.lower()}" for i in attempts[-4:]
            )
            parts.append(
                f"Iteration {last.number} failed verification.\n\n{last.analysis}\n\n"
                f"Files changed so far: {', '.join(last.changed_files) or 'none'}\n\n"
                f"Previous attempts:\n{history}\n\n"
                "Fix the failure above. Do not repeat a change that did not work."
            )
        return "\n\n".join(p for p in parts if p)

    def _invoke_agent(self, run, context_id: int, prompt: str) -> tuple[str, str | None]:
        adapter = self._agent_factory(self._db)
        run = self._require(run.id)
        if run.agent_session_id is None:
            options = {"role": "IMPLEMENTATION"}
            if run.script is not None:
                options["script"] = run.script
            session = adapter.start(
                AgentContext(
                    project_id=run.project_id, working_directory=self._workdir(run),
                    execution_provider="host", execution_target=str(context_id),
                ),
                prompt, options,
            )
            models.update_run(self._db, run.id, agent_session_id=session.id)
            before = 0
        else:
            existing = adapter.stream(run.agent_session_id)
            before = existing[-1].id if existing else 0
            session = adapter.resume(run.agent_session_id, prompt)
        session = adapter.status(session.id)
        events = adapter.stream(session.id, after_id=before or None) if before else adapter.stream(session.id)
        reply = "\n".join(e.data for e in events if e.event_type == "AgentText")
        error = None
        if session.status == "FAILED":
            errors = [e.data for e in events if e.event_type == "AgentError"]
            error = "Agent session failed" + (f": {errors[-1]}" if errors else "")
        return reply, error

    # -- git ----------------------------------------------------------------------------------

    def _git(self, context_id: int, *args: str) -> tuple[int, str]:
        process = self._provider.start_process(
            ["git", *args], {"context_id": context_id, "timeout": 60, "command_summary": "git " + args[0]}
        )
        finished = self._provider.wait(process.id)
        out = "\n".join(
            e.data for e in self._provider.stream_output(process.id)
            if e.event_type == "ProcessOutput" and e.stream != "stderr"
        )
        return finished.exit_code if finished.exit_code is not None else -1, out

    def _snapshot(self, context_id: int) -> tuple[list[str], str]:
        """Changed files and a content-aware signature of the working tree."""
        code, status = self._git(context_id, "status", "--porcelain", "-uall")
        if code != 0:
            return [], "not-a-git-repository"
        workdir = self._provider_workdir(context_id)
        files, digest = [], hashlib.sha256()
        for line in sorted(status.splitlines()):
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            files.append(path)
            digest.update(line.encode())
            target = os.path.join(workdir, path)
            try:
                if os.path.isfile(target) and os.path.getsize(target) <= MAX_SNAPSHOT_FILE_BYTES:
                    with open(target, "rb") as fh:
                        digest.update(fh.read())
            except OSError:
                pass
        return files, digest.hexdigest()

    def _store_diff(self, run, number: int, context_id: int, files: list[str]) -> None:
        """Keep the iteration's Git diff as an artifact (§17). Untracked files
        are listed in a header since `git diff` does not show them."""
        try:
            code, diff = self._git(context_id, "diff")
            if code != 0 or (not diff.strip() and not files):
                return
            header = "# Changed files:\n" + "\n".join(f"#   {f}" for f in files) + "\n\n" if files else ""
            collector.store_bytes(
                self._db, self._engine._root, run.project_id, f"iteration_{number}.diff",
                (header + diff).encode(), os.path.join("ralph", str(run.id), f"iteration_{number}"),
                self._patterns, kind="diff", ralph_run_id=run.id, iteration_number=number,
                step_name=f"iteration {number}", tags=["ralph", "diff"],
            )
        except Exception:  # evidence capture must not break the run
            pass

    def _provider_workdir(self, context_id: int) -> str:
        from app.execution import models as exec_models

        return exec_models.get_execution_context(self._db, context_id).working_directory

    def _complete_or_wait(self, run_id: int, context_id: int, started: float) -> models.RalphRun:
        """Verification passed. Completion still needs every required acceptance
        criterion verified or waived (RUN_AND_RALPH §14); until then the run
        waits for a person, with evidence already suggested for them."""
        acceptance.suggest_for_run(self._db, run_id)
        blockers = acceptance.completion_blockers(self._db, run_id)
        if not blockers:
            return self._finish_success(run_id, context_id, started)
        models.update_run(self._db, run_id, awaiting_acceptance=1, needs_attention=1)
        names = "; ".join(f"{c.title} ({c.status.lower()})" for c in blockers[:5])
        return self._end(
            run_id, "WAITING_FOR_HUMAN",
            f"Verification passed; acceptance criteria need sign-off: {names}", started,
        )

    def finalize(self, run_id: int) -> models.RalphRun:
        """Complete a run that was waiting on acceptance, once its criteria are
        verified or waived: commits (if enabled) and marks it COMPLETED."""
        run = self._require(run_id)
        if run.status != "WAITING_FOR_HUMAN" or not run.awaiting_acceptance:
            raise ValueError("This run is not waiting for acceptance sign-off")
        blockers = acceptance.completion_blockers(self._db, run_id)
        if blockers:
            raise ValueError(
                "Still required: " + "; ".join(f"{c.title} ({c.status.lower()})" for c in blockers[:5])
            )
        context = self._provider.create_context({"working_directory": self._workdir(run)})
        try:
            models.update_run(self._db, run_id, awaiting_acceptance=0, needs_attention=0)
            return self._finish_success(run_id, context.id, self._clock())
        finally:
            self._provider.destroy_context(context.id)

    def _finish_success(self, run_id: int, context_id: int, started: float) -> models.RalphRun:
        run = self._require(run_id)
        if not run.auto_commit:
            return self._end(run_id, "COMPLETED", "Verification passed (auto commit off)", started)
        code, out = self._git(context_id, "status", "--porcelain")
        if code != 0:
            models.update_run(self._db, run_id, needs_attention=1)
            return self._end(run_id, "BLOCKED", "Verification passed but the repository is not a valid git checkout", started)
        if out.strip():
            sprint = ""
            if run.sprint_id:
                row = self._db.execute("SELECT name FROM sprints WHERE id = ?", (run.sprint_id,)).fetchone()
                sprint = row["name"] if row else ""
            message = (
                f"[AUTO] Phase 2 pipeline: {sprint or 'no sprint'} task {run.work_item_id or run.id} "
                f"(iteration {run.current_iteration})"
            )
            self._git(context_id, "add", "-A")
            code, out = self._git(context_id, "commit", "-m", message)
            if code != 0:
                models.update_run(self._db, run_id, needs_attention=1)
                return self._end(
                    run_id, "BLOCKED", f"Verification passed but the commit failed: {out[-300:]}", started
                )
        code, sha = self._git(context_id, "rev-parse", "HEAD")
        sha = sha.strip() if code == 0 else ""
        iterations = models.list_iterations(self._db, run_id)
        if iterations:
            models.update_iteration(self._db, iterations[-1].id, commit_sha=sha)
        models.update_run(self._db, run_id, commit_sha=sha)
        return self._end(run_id, "COMPLETED", "Verification passed" + (f", committed {sha[:8]}" if sha else ""), started)

    # -- analysis ---------------------------------------------------------------------------------

    def _analyse_failure(self, execution_id: int, agent_error: str | None) -> tuple[str, str]:
        """Human-readable evidence for the retry prompt plus a stable signature."""
        lines, sig_parts = [], []
        if agent_error:
            lines.append(agent_error)
            sig_parts.append(agent_error)
        for step in executions.list_steps(self._db, execution_id):
            if step.status in ("FAILED", "TIMED_OUT"):
                detail = (step.error_summary or step.result_summary)[-FEEDBACK_CHARS:]
                lines.append(f"{step.element_type} step '{step.element_name}' {step.status.lower()}"
                             f"{'' if step.exit_code is None else f' (exit code {step.exit_code})'}:\n{detail}")
                sig_parts.append(f"{step.element_name}|{_NOISE.sub('#', detail)[:400]}")
        if not lines:
            ex = executions.get_execution(self._db, execution_id)
            lines.append(f"Verification pipeline {ex.status.lower()}: {ex.reason}")
            sig_parts.append(ex.reason)
        signature = hashlib.sha256("\n".join(sig_parts).encode()).hexdigest()[:16]
        return "\n\n".join(lines), signature

    def _no_progress(self, run: models.RalphRun, iteration_id: int) -> str:
        """Ralph owns no-progress policy across iterations (§10)."""
        recent = models.list_iterations(self._db, run.id)
        failed = [i for i in recent if i.status in ("FAILED", "NO_PROGRESS")]
        n = run.identical_failure_limit
        if len(failed) >= n and len({i.failure_signature for i in failed[-n:]}) == 1:
            return f"No progress: the same failure appeared in {n} consecutive iterations"
        m = run.no_change_limit
        if len(recent) >= m + 1:
            tail = recent[-(m + 1):]
            if len({i.change_signature for i in tail}) == 1:
                return f"No progress: the working tree did not change for {m} iterations"
        return ""

    # -- helpers ----------------------------------------------------------------------------------

    def _flag(self, run_id: int, name: str) -> bool:
        return bool(self._db.execute(f"SELECT {name} FROM ralph_runs WHERE id = ?", (run_id,)).fetchone()[0])

    def _require(self, run_id: int) -> models.RalphRun:
        run = models.get_run(self._db, run_id)
        if run is None:
            raise LookupError(f"Ralph run {run_id} not found")
        return run

    def _workdir(self, run: models.RalphRun) -> str:
        repo = project_models.get_repository(self._db, run.project_id, run.repository_id)
        if repo is None:
            raise ValueError("The repository no longer exists")
        return repo.path
