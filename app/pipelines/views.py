from __future__ import annotations

import csv
import io
import json

from flask import (
    Blueprint,
    Response,
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
from app.pipelines import executions, persistence, replay, visualization
from app.pipelines.manager import PipelineManager
from app.projects import models as project_models
from app.runs import artifacts as run_artifacts

bp = Blueprint("pipelines", __name__, url_prefix="/projects/<int:project_id>/pipelines")

PAGE_SIZE = 25


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _manager() -> PipelineManager:
    return current_app.extensions["pipeline_manager"]


def _root() -> str:
    return run_artifacts.artifact_root(current_app.config)


def _project(project_id: int):
    project = project_models.get_project(get_db(), project_id)
    if project is None:
        abort(404)
    return project


def _execution(project_id: int, execution_id: int):
    ex = executions.get_execution(get_db(), execution_id)
    if ex is None or ex.project_id != project_id:
        abort(404)
    return ex


def _reply(message: str, ok: bool, target: str, code: int = 400, **extra):
    if _wants_json():
        if ok:
            return {"status": "success", "message": message, **extra}, 200
        return {"error": message}, code
    flash(message, "success" if ok else "error")
    return redirect(target)


def _csv_safe(value):
    """Stop spreadsheet apps treating command output as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _variables(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            if key.strip():
                out[key.strip()] = value.strip()
    return out


@bp.get("")
def list_pipelines(project_id: int):
    project = _project(project_id)
    db = get_db()
    page = max(request.args.get("page", 1, type=int), 1)
    return render_template(
        "pipelines/list.html",
        project=project,
        pipelines=[p for p in persistence.list_pipelines(db, project_id) if p.enabled],
        executions=executions.list_executions(db, project_id, PAGE_SIZE, (page - 1) * PAGE_SIZE),
        names={p.id: p.name for p in persistence.list_pipelines(db, project_id)},
        page=page,
    )


@bp.post("/executions")
def start_execution(project_id: int):
    _project(project_id)
    back = url_for("pipelines.list_pipelines", project_id=project_id)
    db = get_db()
    try:
        repo_id = int(request.form.get("repository_id", ""))
    except ValueError:
        return _reply("Choose a repository", False, back)
    if project_models.get_repository(db, project_id, repo_id) is None:
        return _reply("Choose a repository", False, back)
    try:
        from app.pipelines.engine import PipelineEngine

        engine = PipelineEngine(db, _manager().provider, _root())
        execution_id = engine.create(
            request.form.get("pipeline", ""), project_id, repo_id,
            variables=_variables(request.form.get("variables", "")),
        )
    except (LookupError, ValueError) as exc:
        return _reply(str(exc), False, back)
    try:
        queued = _manager().start(execution_id, wait=request.form.get("wait_for_project") == "on")
    except ValueError as exc:  # LockConflict: the project is busy
        return _reply(str(exc), False, back, 409)
    target = url_for("pipelines.view_execution", project_id=project_id, execution_id=execution_id)
    return _reply("Queued: it starts when the project is free" if queued else "Pipeline started", True, target,
                  execution_id=execution_id, queued=queued, redirect=target)


@bp.get("/executions/<int:execution_id>")
def view_execution(project_id: int, execution_id: int):
    project = _project(project_id)
    ex = _execution(project_id, execution_id)
    graph = visualization.build_graph(get_db(), ex, expand=_expand())
    return render_template(
        "pipelines/execution.html", project=project, execution=ex, graph=graph,
        pipeline=persistence.get_pipeline(get_db(), ex.pipeline_id),
        iterations=replay.ralph_iterations(get_db(), ex),
        replay_total=get_db().execute(
            "SELECT COUNT(*) FROM pipeline_events WHERE execution_id = ?", (execution_id,)
        ).fetchone()[0],
    )


@bp.get("/executions/<int:execution_id>/graph.json")
def graph_json(project_id: int, execution_id: int):
    _project(project_id)
    return visualization.build_graph(get_db(), _execution(project_id, execution_id), expand=_expand())


@bp.get("/executions/<int:execution_id>/nodes/<name>.json")
def node_json(project_id: int, execution_id: int, name: str):
    _project(project_id)
    ex = _execution(project_id, execution_id)
    detail = visualization.step_detail(
        get_db(), _root(), ex, name, tuple(current_app.config.get("REDACT_PATTERNS", ()))
    )
    if detail is None:
        abort(404)
    return detail


def _patterns() -> tuple[str, ...]:
    return tuple(current_app.config.get("REDACT_PATTERNS", ()))


def _expand() -> frozenset[str]:
    """Sub-pipelines shown inline (`?expand=a,b` or `*`); the rest are one node each (§20)."""
    return visualization.parse_expand(request.values.get("expand"))


def _int_arg(value, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@bp.get("/executions/<int:execution_id>/replay/events")
def replay_events(project_id: int, execution_id: int):
    """Paginated timeline (§18): every recorded event with its kind, icon and node."""
    _project(project_id)
    replayer = replay.Replayer(get_db(), _execution(project_id, execution_id))
    return replayer.timeline(
        _int_arg(request.args.get("offset"), 0), min(_int_arg(request.args.get("limit"), 200), 1000),
        request.args.get("kind") or None,
    )


@bp.get("/executions/<int:execution_id>/replay/state")
def replay_state(project_id: int, execution_id: int):
    """The graph, artifacts and inspector as they were after event `event`."""
    _project(project_id)
    replayer = replay.Replayer(get_db(), _execution(project_id, execution_id))
    n = _int_arg(request.args.get("event"))
    if n is None or not 0 <= n <= len(replayer):
        return {"error": f"event must be between 0 and {len(replayer)}"}, 400
    return replayer.state_json(n, request.args.get("node") or None, _root(), _patterns(), _expand())


@bp.post("/executions/<int:execution_id>/replay/seek")
def replay_seek(project_id: int, execution_id: int):
    """Move the playhead (`action` = previous | next | first | last | seek) and
    return the state at its new position. The position lives in the browser."""
    _project(project_id)
    replayer = replay.Replayer(get_db(), _execution(project_id, execution_id))
    controller = replay.ReplayController(replayer, _int_arg(request.values.get("current"), 0))
    try:
        position = controller.apply(request.values.get("action", "seek"), _int_arg(request.values.get("event")))
    except ValueError as exc:
        return {"error": str(exc)}, 400
    body = replayer.state_json(position, request.values.get("node") or None, _root(), _patterns(), _expand())
    body.update(at_start=controller.at_start, at_end=controller.at_end)
    return body


@bp.get("/executions/<int:execution_id>/steps/<int:step_id>/log")
def step_log(project_id: int, execution_id: int, step_id: int):
    _project(project_id)
    _execution(project_id, execution_id)
    step = executions.get_step(get_db(), step_id)
    if step is None or step.execution_id != execution_id or not step.raw_data_reference:
        abort(404)
    try:
        path = run_artifacts._resolve_within(_root(), step.raw_data_reference)
    except run_artifacts.ArtifactPathError:
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"{step.element_name}_{step.attempt}.log")


@bp.post("/executions/<int:execution_id>/cancel")
def cancel_execution(project_id: int, execution_id: int):
    _project(project_id)
    _execution(project_id, execution_id)
    back = url_for("pipelines.view_execution", project_id=project_id, execution_id=execution_id)
    try:
        _manager().cancel(execution_id)
    except ValueError as exc:
        return _reply(str(exc), False, back, 409)
    return _reply("Cancelling", True, back)


@bp.post("/executions/<int:execution_id>/steps/<int:step_id>/decision")
def decide(project_id: int, execution_id: int, step_id: int):
    """Approve / reject a waiting manual step, then continue the execution."""
    _project(project_id)
    _execution(project_id, execution_id)
    back = url_for("pipelines.view_execution", project_id=project_id, execution_id=execution_id)
    step = executions.get_step(get_db(), step_id)
    if step is None or step.execution_id != execution_id:
        abort(404)
    try:
        _manager().resolve_manual(
            step_id, request.form.get("decision", ""), request.form.get("by", ""),
            request.form.get("comment", ""), request.form.get("value") or None,
        )
    except ValueError as exc:
        return _reply(str(exc), False, back, 409)
    return _reply("Decision recorded", True, back)


@bp.get("/executions/<int:execution_id>/export.<fmt>")
def export_execution(project_id: int, execution_id: int, fmt: str):
    _project(project_id)
    ex = _execution(project_id, execution_id)
    db = get_db()
    steps = executions.list_steps(db, execution_id)
    if fmt == "json":
        payload = {
            "execution": {k: getattr(ex, k) for k in ("id", "pipeline_id", "pipeline_version", "status", "reason", "warnings", "started_at", "completed_at")},
            "steps": [vars(s) for s in steps],
            "events": [vars(e) for e in executions.list_events(db, execution_id)],
        }
        return Response(json.dumps(payload, indent=2), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=execution_{execution_id}.json"})
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["step", "type", "attempt", "status", "exit_code", "started_at", "completed_at", "summary"])
        for s in steps:
            writer.writerow([_csv_safe(v) for v in (
                s.element_name, s.element_type, s.attempt, s.status, s.exit_code,
                s.started_at, s.completed_at, (s.error_summary or s.result_summary)[:200])])
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=execution_{execution_id}.csv"})
    abort(404)
