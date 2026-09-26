from __future__ import annotations

import shlex

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from app.db import get_db
from app.projects import models as project_models
from app.runs import artifacts, models
from app.runs.executor import RunManager
from app.runs.security import redact

bp = Blueprint("runs", __name__, url_prefix="/projects/<int:project_id>/runs")

PAGE_SIZE = 25
MAX_STEPS = 20
LOG_PREVIEW_BYTES = 64 * 1024
_STATUSES = (
    "CREATED", "RUNNING", "PAUSED", "BLOCKED", "COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT",
)


def _manager() -> RunManager:
    return current_app.extensions["run_manager"]


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _project(project_id: int):
    project = project_models.get_project(get_db(), project_id)
    if project is None:
        abort(404)
    return project


def _run(project_id: int, run_id: int) -> models.Run:
    run = models.get_run(get_db(), run_id)
    if run is None or run.project_id != project_id:
        abort(404)
    return run


def _reply(project_id: int, run_id: int | None, message: str, ok: bool, code: int = 400):
    """JSON for AJAX callers, flash + redirect otherwise."""
    if _wants_json():
        if ok:
            return {"status": "success", "message": message, "run_id": run_id}, 200
        return {"error": message}, code
    flash(message, "success" if ok else "error")
    if run_id is not None:
        return redirect(url_for("runs.view_run", project_id=project_id, run_id=run_id))
    return redirect(url_for("runs.list_runs", project_id=project_id))


def _parse_steps(text: str, timeout: float | None) -> list[dict]:
    steps = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if not shlex.split(line):
                continue
        except ValueError as exc:
            raise ValueError(f"Cannot parse {line!r}: {exc}") from exc
        clean, was_redacted = redact(line, current_app.config.get("REDACT_PATTERNS", ()))
        steps.append(
            {
                "name": clean[:60],
                "command": clean,
                "raw_command": line,
                "command_redacted": was_redacted,
                "timeout_seconds": timeout,
            }
        )
    if not steps:
        raise ValueError("Enter at least one command")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"A run is limited to {MAX_STEPS} steps")
    return steps


@bp.get("")
def list_runs(project_id: int):
    project = _project(project_id)
    db = get_db()
    status = request.args.get("status", "")
    if status not in _STATUSES:
        status = ""
    page = max(request.args.get("page", 1, type=int), 1)
    total = models.count_runs(db, project_id, status or None)
    runs = models.list_runs(
        db, project_id, status or None, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    return render_template(
        "runs/list.html",
        project=project,
        runs=runs,
        status=status,
        statuses=_STATUSES,
        page=page,
        pages=max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1),
        total=total,
        active=any(r.active for r in runs),
    )


@bp.post("")
def create_run(project_id: int):
    project = _project(project_id)
    try:
        repo_id = int(request.form.get("repository_id", ""))
    except ValueError:
        return _reply(project_id, None, "Choose a repository", False)
    repo = project_models.get_repository(get_db(), project_id, repo_id)
    if repo is None:
        return _reply(project_id, None, "Choose a repository", False)
    timeout_raw = request.form.get("timeout", "").strip()
    try:
        timeout = float(timeout_raw) if timeout_raw else None
        if timeout is not None and timeout <= 0:
            raise ValueError("Timeout must be positive")
        steps = _parse_steps(request.form.get("commands", ""), timeout)
    except ValueError as exc:
        return _reply(project_id, None, str(exc), False)

    collect = [
        line.strip() for line in request.form.get("collect", "").splitlines() if line.strip()
    ]
    steps[-1]["collect"] = collect[:20]

    title = request.form.get("title", "").strip()[:120] or steps[0]["name"]
    run_id = models.create_run(get_db(), project.id, repo.id, title, steps)
    # Only the redacted command is persisted; the real one is handed to the
    # worker in memory so commands carrying secrets still run correctly.
    live_commands = {
        seq: step["raw_command"]
        for seq, step in enumerate(steps, start=1)
        if step["command_redacted"]
    }
    try:
        queued = _manager().start(run_id, live_commands, wait=request.form.get("wait_for_project") == "on")
    except ValueError as exc:  # LockConflict: the project is busy
        return _reply(project_id, run_id, str(exc), False, 409)
    return _reply(project_id, run_id, "Queued: it starts when the project is free" if queued else "Run started", True)


@bp.get("/<int:run_id>")
def view_run(project_id: int, run_id: int):
    project = _project(project_id)
    run = _run(project_id, run_id)
    db = get_db()
    root = _manager().artifact_root
    log_previews = {}
    stored = models.list_artifacts(db, run_id)
    for artifact in stored:
        if artifact.kind == "log":
            try:
                log_previews[artifact.step_id] = artifacts.read_artifact(
                    root, artifact, LOG_PREVIEW_BYTES
                ).decode("utf-8", "replace")
            except (OSError, artifacts.ArtifactPathError):
                log_previews[artifact.step_id] = "(log file missing)"
    return render_template(
        "runs/detail.html",
        project=project,
        run=run,
        events=models.list_events(db, run_id),
        artifacts=stored,
        log_previews=log_previews,
        executing=_manager().is_executing(run_id),
    )


@bp.get("/<int:run_id>/artifacts/<int:artifact_id>")
def download_artifact(project_id: int, run_id: int, artifact_id: int):
    _run(project_id, run_id)
    artifact = models.get_artifact(get_db(), run_id, artifact_id)
    if artifact is None:
        abort(404)
    try:
        path = artifacts.artifact_file(_manager().artifact_root, artifact)
    except artifacts.ArtifactPathError:
        abort(404)
    return send_file(path, as_attachment=True, download_name=artifact.name.replace("/", "_"))


def _control(project_id: int, run_id: int, action: str, message: str):
    _run(project_id, run_id)
    try:
        getattr(_manager(), action)(run_id)
    except ValueError as exc:
        return _reply(project_id, run_id, str(exc), False, code=409)
    return _reply(project_id, run_id, message, True)


@bp.post("/<int:run_id>/pause")
def pause_run(project_id: int, run_id: int):
    return _control(project_id, run_id, "pause", "Run paused")


@bp.post("/<int:run_id>/resume")
def resume_run(project_id: int, run_id: int):
    return _control(project_id, run_id, "resume", "Run resumed")


@bp.post("/<int:run_id>/stop")
def stop_run(project_id: int, run_id: int):
    return _control(project_id, run_id, "stop", "Stopping run")


@bp.post("/<int:run_id>/restart")
def restart_run(project_id: int, run_id: int):
    """Start a fresh Run copying this one's steps (Runs are immutable history)."""
    run = _run(project_id, run_id)
    if run.status not in models.RUN_RESTARTABLE_STATUSES:
        return _reply(project_id, run_id, f"A {run.status} run cannot be restarted", False, 409)
    if any(step.command_redacted for step in run.steps):
        return _reply(
            project_id,
            run_id,
            "A command contained secrets that were redacted, so it cannot be re-run. "
            "Create a new run instead.",
            False,
            409,
        )
    steps = [
        {
            "name": s.name,
            "command": s.command,
            "timeout_seconds": s.timeout_seconds,
            "collect": s.collect,
        }
        for s in run.steps
    ]
    new_id = models.create_run(
        get_db(), project_id, run.repository_id, run.title, steps, restart_of=run.id
    )
    try:
        _manager().start(new_id)
    except ValueError as exc:  # LockConflict: the project is busy
        return _reply(project_id, new_id, str(exc), False, 409)
    return _reply(project_id, new_id, "Run restarted as a new run", True)
