"""Deterministic pipeline execution (docs/PIPELINE_ENGINE.md §9-§18).

`PipelineEngine.run` walks a frozen, composed element list with a persisted
cursor. Every decision lives in SQLite (cursor, loop counters, registered
resources, waiting manual step), so a paused execution - or one resumed by a
different process - continues exactly where it stopped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from app.pipelines import composer, executions, persistence
from app.pipelines.executions import DONE_STEP, Execution
from app.projects import models as project_models
from app.runs.artifacts import ArtifactPathError, _resolve_within, _safe_name
from app.runs.security import REDACTION_MARK, redact

COMMAND_TYPES = (
    "COMMAND", "TEST", "PLAYWRIGHT", "GIT", "SCREENSHOT", "ACCEPTANCE",
    "DOCKER_COMMAND", "DOCKER_COMPOSE", "SSH_COMMAND", "FILE_OPERATION",
)
MANUAL_TYPES = ("MANUAL_APPROVAL", "MANUAL_INPUT", "MANUAL_REVIEW")
PHASE_RANK = {"SETUP": 0, "MAIN": 1}
SUMMARY_CHARS = 2000
_VARIABLE = re.compile(r"\$\{([^}]+)\}")

# Small built-in prompt fragments until the prompt library (P2.9) exists.
PROMPT_TEMPLATES = {
    "implement-task": "Implement the task described below. Make the smallest change that "
    "satisfies the acceptance criteria, then stop.\n\n${vars.task}",
    "verify-acceptance": "Verify each acceptance criterion below against the current code and "
    "report which pass.\n\n${vars.task}",
}


class VariableError(ValueError):
    """A `${...}` reference could not be resolved."""


@dataclass
class StepResult:
    status: str
    summary: str = ""
    error: str = ""
    exit_code: int | None = None
    process_id: int | None = None
    session_id: int | None = None
    log: str = ""
    input_reference: str = ""


class PipelineEngine:
    def __init__(
        self,
        db: sqlite3.Connection,
        provider,
        artifact_root: str,
        agent_factory: Callable[[sqlite3.Connection], object] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        extra_patterns: tuple[str, ...] = (),
    ):
        self._db = db
        self._provider = provider
        self._root = artifact_root
        self._agent_factory = agent_factory
        self._sleep = sleep
        self._patterns = extra_patterns
        self._active_process: dict[int, int] = {}

    # -- creating and controlling executions ---------------------------------

    def create(
        self,
        pipeline: str | int,
        project_id: int,
        repository_id: int | None = None,
        sprint_id: int | None = None,
        variables: dict | None = None,
        parent_execution_id: int | None = None,
        run_id: int | None = None,
        version: int | None = None,
    ) -> int:
        """Compose a pipeline and freeze it into a new execution (PENDING)."""
        db = self._db
        record = (
            persistence.get_pipeline(db, pipeline)
            if isinstance(pipeline, int)
            else persistence.find_pipeline(db, pipeline, project_id)
        )
        if record is None:
            raise LookupError(f"Pipeline {pipeline!r} not found")
        if not record.enabled:
            raise ValueError(f"Pipeline {record.name!r} is disabled")
        definition = persistence.get_definition(db, record.id, version)
        elements = composer.compose(definition, persistence.resolver_for(db, project_id))
        return executions.create_execution(
            db, record.id, definition["version"], project_id, elements, repository_id,
            sprint_id, parent_execution_id, run_id, variables,
        )

    def cancel(self, execution_id: int) -> None:
        ex = self._require(execution_id)
        if ex.terminal:
            raise ValueError(f"Execution is already {ex.status.lower()}")
        executions.update_execution(self._db, execution_id, cancel_requested=1)
        pid = self._active_process.get(execution_id)
        if pid is not None:
            self._provider.stop_process(pid)

    def active_process(self, execution_id: int) -> int | None:
        return self._active_process.get(execution_id)

    def resolve_manual(
        self, step_id: int, decision: str, by: str, comment: str = "", value: str | None = None
    ) -> None:
        """Answer a waiting manual step. Call `run` afterwards to continue.

        APPROVED passes the step (MANUAL_INPUT records `value` as its output);
        REJECTED fails it so the step's compensation policy applies - e.g. a
        review's LOOP back to the mockup step for "request changes" (§16).
        """
        if decision not in ("APPROVED", "REJECTED"):
            raise ValueError("Decision must be APPROVED or REJECTED")
        if not by.strip():
            raise ValueError("Enter your name to record the decision")
        step = executions.get_step(self._db, step_id)
        if step is None or step.status != "WAITING":
            raise ValueError("That step is not waiting for a decision")
        element = self._element(self._require(step.execution_id), step.element_name)
        if element["type"] == "MANUAL_INPUT" and decision == "APPROVED" and value is None:
            raise ValueError("Provide the requested input")
        text = f"{decision.lower()} by {by.strip()}" + (f": {comment.strip()}" if comment.strip() else "")
        clean, redacted = redact(text, self._patterns)
        status = "PASSED" if decision == "APPROVED" else "FAILED"
        # A MANUAL_INPUT's output is exactly the supplied value so later steps
        # can use `${steps.<name>.output}`; the decision text goes to the event.
        answer = redact(value, self._patterns)[0] if value is not None else clean
        redacted = redacted or (value is not None and answer != value)
        executions.finish_step(
            self._db, step_id, status,
            result_summary=(answer if status == "PASSED" else ""),
            error_summary=(clean if status == "FAILED" else ""),
            redacted=int(redacted),
        )
        executions.add_event(
            self._db, step.execution_id, "ManualApprovalReceived", clean, step_id
        )

    # -- running ---------------------------------------------------------------

    def run(self, execution_id: int) -> Execution:
        """Run (or resume) an execution until it finishes or pauses for a person."""
        db = self._db
        ex = self._require(execution_id)
        if ex.terminal:
            return ex
        elements = ex.resolved_configuration["elements"]
        main = sorted(
            (e for e in elements if e.get("phase", "MAIN") != "TEARDOWN"),
            key=lambda e: PHASE_RANK.get(e.get("phase", "MAIN"), 1),
        )
        teardown = [e for e in elements if e.get("phase") == "TEARDOWN"]

        first = ex.status == "PENDING"
        executions.update_execution(
            db, execution_id, status="RUNNING",
            **({"started_at": _now()} if first else {}),
        )
        executions.add_event(db, execution_id, "PipelineStarted" if first else "PipelineResumed")

        failure: str | None = None
        cancelled = False
        try:
            context_id = self._ensure_context(ex)
            ex = self._require(execution_id)
            cursor = ex.cursor
            pending_outcome: tuple[str, dict, int] | None = None
            if ex.waiting_step_id and not ex.cancel_requested:
                step = executions.get_step(db, ex.waiting_step_id)
                if step.status == "WAITING":
                    executions.update_execution(db, execution_id, status="PAUSED")
                    return self._require(execution_id)
                pending_outcome = (step.status, self._element(ex, step.element_name), step.id)
                executions.update_execution(db, execution_id, waiting_step_id=None)

            while True:
                if self._require(execution_id).cancel_requested:
                    cancelled = True
                    break
                if pending_outcome is not None:
                    status, element, step_id = pending_outcome
                    pending_outcome = None
                else:
                    if cursor >= len(main):
                        break
                    element = main[cursor]
                    status, step_id = self._execute_element(ex, element, context_id)
                    if status == "WAITING":
                        executions.update_execution(
                            db, execution_id, status="PAUSED", cursor=cursor, waiting_step_id=step_id
                        )
                        executions.add_event(
                            db, execution_id, "ManualApprovalRequested", element["name"], step_id
                        )
                        return self._require(execution_id)
                if status in DONE_STEP:
                    cursor = self._index(main, element["name"], cursor) + 1
                elif status == "CANCELLED":
                    cancelled = True
                    break
                else:
                    nxt, failure = self._compensate(ex, element, step_id, main, context_id)
                    if failure is not None:
                        break
                    cursor = nxt
                executions.update_execution(db, execution_id, cursor=cursor)
        except Exception as exc:  # never leave an execution stuck RUNNING
            failure = f"Internal error: {exc}"

        self._teardown(ex, teardown, self._require(execution_id).context_id)
        return self._finish(execution_id, failure, cancelled)

    # -- element execution ------------------------------------------------------

    def _execute_element(self, ex: Execution, element: dict, context_id: int) -> tuple[str, int]:
        """Run one element with its retry policy. Returns (status, step id)."""
        db = self._db
        name = element["name"]
        enabled = element.get("enabled", "ENABLED")
        if enabled != "ENABLED":
            step_id = executions.add_step(db, ex.id, element, self._attempt(ex.id, name), enabled)
            executions.finish_step(db, step_id, enabled)
            executions.add_event(
                db, ex.id, "StepDisabled" if enabled == "DISABLED" else "StepSkipped", name, step_id
            )
            return enabled, step_id
        blocked = [
            d for d in element.get("depends_on") or []
            if (s := executions.latest_step(db, ex.id, d)) is None or s.status not in DONE_STEP
        ]
        if blocked:
            step_id = executions.add_step(db, ex.id, element, self._attempt(ex.id, name), "SKIPPED")
            executions.finish_step(
                db, step_id, "SKIPPED", error_summary="Dependency did not pass: " + ", ".join(blocked)
            )
            executions.add_event(db, ex.id, "StepSkipped", name, step_id)
            return "SKIPPED", step_id

        comp = element.get("compensation") or {}
        attempts = max(int(comp.get("attempts", 1)), 1)
        delay = float(comp.get("delay_seconds", 0))
        status, step_id = "FAILED", 0
        for n in range(attempts):
            status, step_id = self._run_once(ex, element, context_id)
            if status in ("PASSED", "WAITING", "CANCELLED") or n == attempts - 1:
                break
            wait = delay * (2**n if comp.get("backoff") == "exponential" else 1)
            if wait:
                self._sleep(wait)
        return status, step_id

    def _attempt(self, execution_id: int, name: str) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM step_executions WHERE execution_id = ? AND element_name = ?",
            (execution_id, name),
        ).fetchone()[0] + 1

    def _run_once(self, ex: Execution, element: dict, context_id: int) -> tuple[str, int]:
        db = self._db
        step_id = executions.add_step(db, ex.id, element, self._attempt(ex.id, element["name"]))
        executions.add_event(db, ex.id, "StepStarted", element["name"], step_id)
        try:
            handler = self._handler(element["type"])
            result = handler(self._require(ex.id), element, step_id, context_id)
        except (VariableError, ValueError, OSError, LookupError) as exc:
            result = StepResult("FAILED", error=str(exc))
        except Exception as exc:  # a broken handler must fail the step, not the engine
            result = StepResult("FAILED", error=f"Internal error: {exc}")

        summary, was_redacted = redact(result.summary[-SUMMARY_CHARS:], self._patterns)
        error, err_redacted = redact(result.error[-SUMMARY_CHARS:], self._patterns)
        log, log_redacted = redact(result.log, self._patterns)
        reference = ""
        if log:
            reference = self._write_log(ex.id, step_id, element["name"], log)
        executions.update_step(
            db, step_id,
            input_reference=redact(result.input_reference, self._patterns)[0],
            raw_data_reference=reference, exit_code=result.exit_code,
            process_id=result.process_id, session_id=result.session_id,
            redacted=int(was_redacted or err_redacted or log_redacted or REDACTION_MARK in log),
            result_summary=summary, error_summary=error,
        )
        if result.status == "WAITING":
            executions.update_step(db, step_id, status="WAITING")
            return "WAITING", step_id
        executions.finish_step(db, step_id, result.status)
        executions.add_event(
            db, ex.id, "StepCompleted" if result.status == "PASSED" else "StepFailed",
            element["name"] if result.status == "PASSED" else (error or result.status), step_id,
        )
        return result.status, step_id

    def _write_log(self, execution_id: int, step_id: int, name: str, log: str) -> str:
        relative = os.path.join("pipelines", str(execution_id), f"step_{step_id}_{_safe_name(name)}.log")
        target = _resolve_within(self._root, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(log)
        return relative

    # -- compensation -------------------------------------------------------------

    def _compensate(
        self, ex: Execution, element: dict, step_id: int, main: list[dict], context_id: int
    ) -> tuple[int, str | None]:
        """Apply the failed element's policy. Returns (next cursor, None) to
        carry on or (_, reason) to stop the pipeline."""
        db = self._db
        comp = element.get("compensation") or {}
        action = comp.get("action", "STOP")
        name = element["name"]
        index = self._index(main, name, 0)
        reason = f"Step {name!r} failed"
        executions.add_event(db, ex.id, "CompensationStarted", f"{name}: {action}", step_id)

        if action == "CONTINUE":
            fresh = self._require(ex.id)
            executions.update_execution(db, ex.id, warnings=fresh.warnings + 1)
            return index + 1, None

        if action == "LOOP":
            target = comp.get("step")
            names = [e["name"] for e in main]
            if target not in names:
                return index, f"{reason}; loop target {target!r} is not part of the main sequence"
            fresh = self._require(ex.id)
            loops = dict(fresh.loops)
            loops[name] = loops.get(name, 0) + 1
            executions.update_execution(db, ex.id, loops=loops)
            if loops[name] > int(comp["max_loops"]):
                return index, f"{reason}; gave up after {comp['max_loops']} loops"
            if self._no_progress(ex.id, name):
                return index, f"{reason}; no progress between attempts (identical failure output)"
            if comp.get("delay_seconds"):
                self._sleep(float(comp["delay_seconds"]))
            executions.add_event(db, ex.id, "LoopBack", f"{name} -> {target} (loop {loops[name]})", step_id)
            return names.index(target), None

        if action == "RUN_STEP":
            target = self._element(ex, comp.get("step"))
            status, _ = self._run_once(ex, {**target, "enabled": "ENABLED"}, context_id)
            executions.add_event(db, ex.id, "CompensationCompleted", f"{target['name']}: {status}", step_id)
        elif action == "START_PIPELINE":
            child = self.create(
                comp["pipeline"], ex.project_id, ex.repository_id, ex.sprint_id,
                ex.variables, parent_execution_id=ex.id,
            )
            executions.add_event(db, ex.id, "SubPipelineStarted", str(child), step_id)
            outcome = self.run(child)
            executions.add_event(db, ex.id, "SubPipelineCompleted", f"{child}: {outcome.status}", step_id)
        elif action == "STOP_AND_MESSAGE":
            executions.update_execution(db, ex.id, needs_attention=1)
            executions.add_event(db, ex.id, "InterventionRequested", reason, step_id)
        return index, reason

    def _no_progress(self, execution_id: int, name: str) -> bool:
        """True when the last two failures of `name` produced identical output."""
        rows = self._db.execute(
            "SELECT error_summary, result_summary, exit_code FROM step_executions "
            "WHERE execution_id = ? AND element_name = ? AND status = 'FAILED' ORDER BY id DESC LIMIT 2",
            (execution_id, name),
        ).fetchall()
        if len(rows) < 2:
            return False
        digest = [hashlib.sha256(json.dumps(list(r)).encode()).hexdigest() for r in rows]
        return digest[0] == digest[1]

    # -- teardown and finishing ----------------------------------------------------

    def _teardown(self, ex: Execution, teardown: list[dict], context_id: int | None) -> None:
        """Finally-style: always runs, failures only add warnings (§15)."""
        db = self._db
        for element in teardown:
            status, _ = self._execute_element(ex, element, context_id)
            if status == "FAILED":
                fresh = self._require(ex.id)
                executions.update_execution(db, ex.id, warnings=fresh.warnings + 1)
        fresh = self._require(ex.id)
        for resource in list(fresh.resources):
            self._provider.stop_process(resource["process_id"])
            executions.add_event(db, ex.id, "ResourceCleanup", resource["name"])
        executions.update_execution(db, ex.id, resources=[])
        if context_id is not None:
            try:
                self._provider.destroy_context(context_id)
            except Exception:  # cleanup must not mask the real outcome
                pass

    def _finish(self, execution_id: int, failure: str | None, cancelled: bool) -> Execution:
        db = self._db
        if cancelled:
            status, reason = "CANCELLED", "Stopped by user"
        elif failure:
            status, reason = "FAILED", failure
        else:
            status, reason = "COMPLETED", ""
        fresh = self._require(execution_id)
        if status == "COMPLETED" and fresh.warnings:
            reason = f"Completed with {fresh.warnings} warning(s)"
        db.execute(
            "UPDATE step_executions SET status = 'CANCELLED', completed_at = ? "
            "WHERE execution_id = ? AND status IN ('WAITING', 'RUNNING')",
            (_now(), execution_id),
        )
        executions.update_execution(
            db, execution_id, status=status, reason=reason, completed_at=_now(), waiting_step_id=None
        )
        executions.add_event(db, execution_id, f"Pipeline{status.title()}", reason)
        return self._require(execution_id)

    # -- context / lookup helpers ---------------------------------------------------

    def _ensure_context(self, ex: Execution) -> int:
        if ex.context_id:
            return ex.context_id
        repo = (
            project_models.get_repository(self._db, ex.project_id, ex.repository_id)
            if ex.repository_id
            else None
        )
        if repo is None:
            raise ValueError("The pipeline needs a repository to run in")
        context = self._provider.create_context({"working_directory": repo.path})
        executions.update_execution(self._db, ex.id, context_id=context.id)
        return context.id

    def _require(self, execution_id: int) -> Execution:
        ex = executions.get_execution(self._db, execution_id)
        if ex is None:
            raise LookupError(f"Execution {execution_id} not found")
        return ex

    @staticmethod
    def _element(ex: Execution, name: str | None) -> dict:
        for element in ex.resolved_configuration["elements"]:
            if element["name"] == name:
                return element
        raise LookupError(f"Element {name!r} is not part of this execution")

    @staticmethod
    def _index(main: list[dict], name: str, default: int) -> int:
        for i, e in enumerate(main):
            if e["name"] == name:
                return i
        return default

    def _workdir(self, ex: Execution) -> str:
        repo = project_models.get_repository(self._db, ex.project_id, ex.repository_id or 0)
        if repo is None:
            raise ValueError("The pipeline needs a repository to run in")
        return repo.path

    # -- variables ---------------------------------------------------------------------

    def _resolve(self, ex: Execution, text: str) -> str:
        """Substitute `${project.path}`, `${execution.id}`, `${run.id}`, `${sprint.id}`,
        `${steps.NAME.exit_code|process_id|output}` and `${vars.KEY}` (§13)."""

        def lookup(match: re.Match) -> str:
            key = match.group(1).strip()
            head, _, rest = key.partition(".")
            if key == "project.path":
                return self._workdir(ex)
            if key == "project.id":
                return str(ex.project_id)
            if key == "execution.id":
                return str(ex.id)
            if key == "run.id" and ex.run_id is not None:
                return str(ex.run_id)
            if key == "sprint.id" and ex.sprint_id is not None:
                return str(ex.sprint_id)
            if head == "vars" and rest in ex.variables:
                return str(ex.variables[rest])
            if head == "steps":
                name, _, attr = rest.rpartition(".")
                step = executions.latest_step(self._db, ex.id, name)
                if step is not None and attr in ("exit_code", "process_id"):
                    value = getattr(step, attr)
                    if value is not None:
                        return str(value)
                if step is not None and attr == "output":
                    return step.result_summary
            raise VariableError(f"Unknown variable ${{{key}}}")

        return _VARIABLE.sub(lookup, text)

    # -- handlers ---------------------------------------------------------------------

    def _handler(self, element_type: str):
        if element_type in COMMAND_TYPES:
            return self._h_command
        if element_type in MANUAL_TYPES:
            return self._h_manual
        table = {
            "AGENT": self._h_agent,
            "WAIT": self._h_wait,
            "PROCESS_START": self._h_process_start,
            "PROCESS_STOP": self._h_process_stop,
            "HEALTHCHECK": self._h_healthcheck,
            "HTTP_REQUEST": self._h_http,
            "FILE_CHECK": self._h_file_check,
            "ARTIFACT_CAPTURE": self._h_capture,
        }
        if element_type not in table:
            raise ValueError(f"Element type {element_type} cannot be executed")
        return table[element_type]

    def _run_command(self, ex: Execution, command_text: str, context_id: int, element: dict, wait=True):
        argv = shlex.split(self._resolve(ex, command_text))
        if not argv:
            raise ValueError("The element has an empty command")
        process = self._provider.start_process(
            argv,
            {
                "context_id": context_id,
                "timeout": (element.get("config") or {}).get("timeout"),
                "command_summary": redact(command_text, self._patterns)[0],
            },
        )
        if not wait:
            return process, None
        self._active_process[ex.id] = process.id
        try:
            finished = self._provider.wait(process.id)
        finally:
            self._active_process.pop(ex.id, None)
        log = "\n".join(
            (f"[stderr] {e.data}" if e.stream == "stderr" else e.data)
            for e in self._provider.stream_output(process.id)
            if e.event_type == "ProcessOutput"
        )
        return finished, log

    def _h_command(self, ex, element, step_id, context_id) -> StepResult:
        config = element.get("config") or {}
        command = config.get("command")
        if not command:
            return StepResult("FAILED", error=f"{element['type']} element has no command configured")
        finished, log = self._run_command(ex, command, context_id, element)
        base = dict(
            exit_code=finished.exit_code, process_id=finished.id, log=log, input_reference=command,
            summary=log[-SUMMARY_CHARS:],
        )
        if finished.status == "TIMED_OUT":
            return StepResult("TIMED_OUT", error="Timed out", **base)
        if finished.status == "STOPPED":
            return StepResult("CANCELLED", error="Stopped", **base)
        if finished.exit_code != 0:
            return StepResult("FAILED", error=f"Exit code {finished.exit_code}\n{log[-500:]}", **base)
        if config.get("expect_empty") and log.strip():
            return StepResult("FAILED", error="Expected no output but got:\n" + log[-500:], **base)
        return StepResult("PASSED", **base)

    def _h_process_start(self, ex, element, step_id, context_id) -> StepResult:
        config = element.get("config") or {}
        process, _ = self._run_command(ex, config["command"], context_id, element, wait=False)
        fresh = self._require(ex.id)
        resources = [*fresh.resources, {"name": config.get("process", element["name"]), "process_id": process.id}]
        executions.update_execution(self._db, ex.id, resources=resources)
        return StepResult(
            "PASSED", summary=f"Started process {process.id}", process_id=process.id,
            input_reference=config["command"],
        )

    def _h_process_stop(self, ex, element, step_id, context_id) -> StepResult:
        target = (element.get("config") or {}).get("process")
        fresh = self._require(ex.id)
        keep, stopped = [], []
        for resource in fresh.resources:
            (stopped if resource["name"] == target else keep).append(resource)
        for resource in stopped:
            self._provider.stop_process(resource["process_id"])
        executions.update_execution(self._db, ex.id, resources=keep)
        return StepResult("PASSED", summary=f"Stopped {len(stopped)} process(es) named {target!r}")

    def _h_healthcheck(self, ex, element, step_id, context_id) -> StepResult:
        config = element.get("config") or {}
        if config.get("url"):
            return self._h_http(ex, {**element, "config": {**config, "method": "GET"}}, step_id, context_id)
        return self._h_command(ex, element, step_id, context_id)

    def _h_http(self, ex, element, step_id, context_id) -> StepResult:
        config = element.get("config") or {}
        url = self._resolve(ex, config["url"])
        body = self._resolve(ex, config["body"]).encode() if config.get("body") else None
        request = urllib.request.Request(
            url, data=body, method=config.get("method", "GET"),
            headers={k: self._resolve(ex, str(v)) for k, v in (config.get("headers") or {}).items()},
        )
        expected = config.get("expect_status")
        try:
            with urllib.request.urlopen(request, timeout=float(config.get("timeout", 10))) as response:
                status, text = response.status, response.read(4096).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, text = exc.code, ""
        except (urllib.error.URLError, OSError) as exc:
            return StepResult("FAILED", error=f"Request failed: {exc}", input_reference=f"{request.method} {url}")
        ok = status in expected if expected else 200 <= status < 300
        ref = f"{request.method} {url}"
        return StepResult(
            "PASSED" if ok else "FAILED", summary=f"HTTP {status}\n{text}",
            error="" if ok else f"Unexpected HTTP status {status}", input_reference=ref,
        )

    def _h_wait(self, ex, element, step_id, context_id) -> StepResult:
        seconds = float((element.get("config") or {})["seconds"])
        self._sleep(seconds)
        return StepResult("PASSED", summary=f"Waited {seconds:g}s")

    def _h_file_check(self, ex, element, step_id, context_id) -> StepResult:
        path = self._resolve(ex, (element.get("config") or {})["path"])
        try:
            target = _resolve_within(self._workdir(ex), path)
        except ArtifactPathError:
            return StepResult("FAILED", error="Path escapes the repository")
        exists = os.path.exists(target)
        return StepResult(
            "PASSED" if exists else "FAILED", summary=path if exists else "",
            error="" if exists else f"{path} does not exist", input_reference=path,
        )

    def _h_capture(self, ex, element, step_id, context_id) -> StepResult:
        """Copy the configured files into this step's artifact directory."""
        patterns = (element.get("config") or {}).get("paths") or []
        base = self._workdir(ex)
        copied = []
        for pattern in patterns:
            try:
                source = _resolve_within(base, self._resolve(ex, pattern))
            except ArtifactPathError:
                continue
            if os.path.isfile(source):
                relative = os.path.join("pipelines", str(ex.id), f"step_{step_id}", _safe_name(source))
                target = _resolve_within(self._root, relative)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(source, target)
                copied.append(relative)
        return StepResult(
            "PASSED" if copied or not patterns else "FAILED", summary="\n".join(copied),
            error="" if copied or not patterns else "No files matched", log="\n".join(copied),
        )

    def _h_manual(self, ex, element, step_id, context_id) -> StepResult:
        prompt = self._resolve(ex, (element.get("config") or {}).get("prompt", ""))
        return StepResult("WAITING", summary=prompt, input_reference=prompt)

    def _h_agent(self, ex, element, step_id, context_id) -> StepResult:
        if self._agent_factory is None:
            raise ValueError("No agent is configured for this engine")
        from app.agents.base import AgentContext

        config = element.get("config") or {}
        template = config.get("prompt") or PROMPT_TEMPLATES.get(config.get("prompt_template", ""))
        if not template:
            raise ValueError(f"Unknown prompt template {config.get('prompt_template')!r}")
        prompt = self._resolve(ex, template)
        adapter = self._agent_factory(self._db)
        options = {"role": config.get("role", "IMPLEMENTATION")}
        if config.get("script") is not None:
            options["script"] = config["script"]
        session = adapter.start(
            AgentContext(
                project_id=ex.project_id, working_directory=self._workdir(ex),
                execution_provider="host", execution_target=str(context_id),
            ),
            prompt, options,
        )
        session = adapter.status(session.id)
        reply = "\n".join(e.data for e in adapter.stream(session.id) if e.event_type == "AgentText")
        # The effective prompt is the input reference: stored for every execution (§7, §11).
        base = dict(session_id=session.id, input_reference=prompt, summary=reply, log=reply)
        if session.status == "COMPLETED":
            return StepResult("PASSED", **base)
        return StepResult("FAILED", error=f"Agent session ended {session.status.lower()}", **base)


def _now() -> str:
    from app.runs.models import now

    return now()
