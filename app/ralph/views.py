from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from app.db import get_db
from app.pipelines import executions, persistence
from app.projects import models as project_models
from app.ralph import models
from app.ralph import timeline as timeline_mod
from app.ralph.manager import RalphManager

bp = Blueprint("ralph", __name__, url_prefix="/projects/<int:project_id>/ralph")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _manager() -> RalphManager:
    return current_app.extensions["ralph_manager"]


def _project(project_id: int):
    project = project_models.get_project(get_db(), project_id)
    if project is None:
        abort(404)
    return project


def _run(project_id: int, run_id: int):
    run = models.get_run(get_db(), run_id)
    if run is None or run.project_id != project_id:
        abort(404)
    return run


def _reply(message: str, ok: bool, target: str, code: int = 400, **extra):
    if _wants_json():
        if ok:
            return {"status": "success", "message": message, **extra}, 200
        return {"error": message}, code
    flash(message, "success" if ok else "error")
    return redirect(target)


def _detail(project_id: int, run_id: int) -> str:
    return url_for("ralph.view_run", project_id=project_id, run_id=run_id)


@bp.get("")
def list_runs(project_id: int):
    project = _project(project_id)
    db = get_db()
    return render_template(
        "ralph/list.html", project=project, runs=models.list_runs(db, project_id),
        pipelines=[p for p in persistence.list_pipelines(db, project_id) if p.enabled],
    )


@bp.post("")
def start_run(project_id: int):
    _project(project_id)
    db = get_db()
    back = url_for("ralph.list_runs", project_id=project_id)
    try:
        repo_id = int(request.form.get("repository_id", ""))
        if project_models.get_repository(db, project_id, repo_id) is None:
            raise ValueError
    except ValueError:
        return _reply("Choose a repository", False, back)

    task = request.form.get("task", "").strip()
    if not task:
        return _reply("Describe the task", False, back)

    def number(name, default, cast):
        raw = request.form.get(name, "").strip()
        return cast(raw) if raw else default

    try:
        run_id = models.create_run(
            db, project_id, repo_id,
            request.form.get("title", ""), task,
            request.form.get("verification_pipeline", ""),
            acceptance=[l.strip() for l in request.form.get("acceptance", "").splitlines() if l.strip()],
            max_iterations=number("max_iterations", models.DEFAULT_MAX_ITERATIONS, int),
            max_runtime_seconds=number("max_runtime_seconds", None, float),
            auto_commit=request.form.get("auto_commit") == "on",
        )
    except ValueError as exc:
        return _reply(str(exc), False, back)
    try:
        queued = _manager().start(run_id, wait=request.form.get("wait_for_project") == "on")
    except ValueError as exc:  # LockConflict: the run stays CREATED and can be resumed
        return _reply(str(exc), False, _detail(project_id, run_id), 409, run_id=run_id, redirect=_detail(project_id, run_id))
    return _reply("Queued: Ralph starts when the project is free" if queued else "Ralph started", True,
                  _detail(project_id, run_id), run_id=run_id, queued=queued, redirect=_detail(project_id, run_id))


@bp.get("/<int:run_id>")
def view_run(project_id: int, run_id: int):
    project = _project(project_id)
    run = _run(project_id, run_id)
    db = get_db()
    iterations = models.list_iterations(db, run_id)
    return render_template(
        "ralph/detail.html", project=project, run=run, iterations=iterations,
        steering=models.list_steering(db, run_id),
        executing=_manager().is_executing(run_id),
        executions={
            i.verification_execution_id: executions.get_execution(db, i.verification_execution_id)
            for i in iterations if i.verification_execution_id
        },
    )


@bp.get("/<int:run_id>/status.json")
def status_json(project_id: int, run_id: int):
    _project(project_id)
    run = _run(project_id, run_id)
    return {
        "status": run.status, "reason": run.reason, "iteration": run.current_iteration,
        "needs_attention": run.needs_attention,
        "iterations": [{"number": i.number, "status": i.status} for i in models.list_iterations(get_db(), run_id)],
    }


def _control(project_id: int, run_id: int, action: str, message: str, *args):
    _project(project_id)
    _run(project_id, run_id)
    back = _detail(project_id, run_id)
    try:
        getattr(_manager(), action)(run_id, *args)
    except ValueError as exc:
        return _reply(str(exc), False, back, 409)
    return _reply(message, True, back)


@bp.post("/<int:run_id>/pause")
def pause(project_id: int, run_id: int):
    return _control(project_id, run_id, "pause", "Pausing at the next iteration boundary")


@bp.post("/<int:run_id>/resume")
def resume(project_id: int, run_id: int):
    _project(project_id)
    run = _run(project_id, run_id)
    back = _detail(project_id, run_id)
    if run.status not in ("PAUSED", "CREATED") or _manager().is_executing(run_id):
        return _reply(f"A {run.status.lower()} run cannot be resumed", False, back, 409)
    try:
        _manager().start(run_id)
    except ValueError as exc:
        return _reply(str(exc), False, back, 409)
    return _reply("Resumed", True, back)


@bp.post("/<int:run_id>/cancel")
def cancel(project_id: int, run_id: int):
    return _control(project_id, run_id, "cancel", "Cancelling")


@bp.post("/<int:run_id>/steer")
def steer(project_id: int, run_id: int):
    return _control(project_id, run_id, "steer", "Steering saved for the next iteration", request.form.get("message", ""))


@bp.post("/<int:run_id>/complete")
def complete(project_id: int, run_id: int):
    """Finish a run whose acceptance criteria are now signed off."""
    return _control(project_id, run_id, "finalize", "Run completed")


@bp.post("/<int:run_id>/unblock")
def unblock(project_id: int, run_id: int):
    return _control(project_id, run_id, "unblock", "Continuing", request.form.get("message", ""))


@bp.get("/<int:run_id>/timeline")
def timeline(project_id: int, run_id: int):
    """One lane per iteration and the merged event list (PIPELINE_VISUALISATION §19)."""
    project = _project(project_id)
    run = _run(project_id, run_id)
    data = timeline_mod.merged_timeline(get_db(), run_id, include_hidden=request.args.get("all") == "1")
    if _wants_json():
        return data
    return render_template("ralph/timeline.html", project=project, run=run, **data)


@bp.get("/<int:run_id>/compare")
def compare(project_id: int, run_id: int):
    """Two iterations side by side; defaults to the last two."""
    project = _project(project_id)
    run = _run(project_id, run_id)
    numbers = [i.number for i in models.list_iterations(get_db(), run_id)]
    if len(numbers) < 2:
        flash("A run needs at least two iterations to compare", "error")
        return redirect(_detail(project_id, run_id))
    try:
        a = int(request.args.get("a") or numbers[-2])
        b = int(request.args.get("b") or numbers[-1])
    except ValueError:
        abort(400)
    result = timeline_mod.compare(get_db(), run_id, a, b)
    if result is None:
        abort(404)
    return render_template("ralph/compare.html", project=project, run=run, numbers=numbers, **result)
