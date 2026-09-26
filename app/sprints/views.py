from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from app.backlog import persistence as backlog
from app.db import get_db
from app.projects import models as project_models
from app.sprints import persistence as sprints
from app.sprints import workflow
from app.sprints.models import PLANNING_PROFILES, NotReadyError, InvalidSprintTransitionError
from app.sprints.planning_agent import build_context, build_planning_agent

bp = Blueprint("sprints", __name__, url_prefix="/projects/<int:project_id>/sprints")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _project(project_id: int):
    project = project_models.get_project(get_db(), project_id)
    if project is None:
        abort(404)
    return project


def _sprint(project_id: int, sprint_id: int):
    sprint = sprints.get_sprint(get_db(), sprint_id)
    if sprint is None or sprint.project_id != project_id:
        abort(404)
    return sprint


def _reply(message: str, ok: bool, target: str, code: int = 400, **extra):
    if _wants_json():
        if ok:
            return {"status": "success", "message": message, **extra}, 200
        return {"error": message}, code
    flash(message, "success" if ok else "error")
    return redirect(target)


def _detail_url(project_id: int, sprint_id: int) -> str:
    return url_for("sprints.view_sprint", project_id=project_id, sprint_id=sprint_id)


def _agent():
    return build_planning_agent(current_app.config, get_db())


def _context(project):
    """Run in the primary repository (or the first allowed root for a
    repository-less project)."""
    repo = next((r for r in project.repositories if r.is_primary), None) or (
        project.repositories[0] if project.repositories else None
    )
    path = repo.path if repo else current_app.config["ALLOWED_PROJECT_ROOTS"][0]
    return build_context(current_app.config, project.id, path)


def _guard(fn, back: str):
    """Run a workflow call, mapping domain errors to a failed reply."""
    try:
        return fn(), None
    except (ValueError, NotReadyError, InvalidSprintTransitionError, workflow.PlanningFailed) as exc:
        return None, _reply(str(exc), False, back, 409)


@bp.get("")
def list_sprints(project_id: int):
    project = _project(project_id)
    return render_template(
        "sprints/list.html", project=project, sprints=sprints.list_sprints(get_db(), project_id)
    )


@bp.get("/new")
def new_sprint(project_id: int):
    project = _project(project_id)
    candidates = [
        i
        for status in ("TRIAGED", "INBOX")
        for i in backlog.list_items(get_db(), project_id, status=status, limit=200)
        if i.sprint_id is None
    ]
    return render_template(
        "sprints/planning.html", project=project, candidates=candidates, profiles=PLANNING_PROFILES
    )


@bp.post("")
def create_sprint(project_id: int):
    _project(project_id)
    db = get_db()
    back = url_for("sprints.new_sprint", project_id=project_id)
    try:
        sprint_id = sprints.create_sprint(
            db,
            project_id,
            request.form.get("name", ""),
            goal=request.form.get("goal", ""),
            description=request.form.get("description", ""),
            planning_profile=request.form.get("planning_profile", "STANDARD_FEATURE"),
            start_date=request.form.get("start_date"),
            target_date=request.form.get("target_date"),
        )
    except ValueError as exc:
        return _reply(str(exc), False, back)
    ids = [int(v) for v in request.form.getlist("item_ids") if v.isdigit()]
    added, failed = _guard(lambda: workflow.select_items(db, sprint_id, ids), _detail_url(project_id, sprint_id))
    if failed:
        return failed
    for line in request.form.get("documents", "").splitlines():
        if line.strip():
            sprints.add_document_ref(db, sprint_id, line)
    target = _detail_url(project_id, sprint_id)
    return _reply("Sprint created", True, target, sprint_id=sprint_id, redirect=target)


@bp.get("/<int:sprint_id>")
def view_sprint(project_id: int, sprint_id: int):
    project = _project(project_id)
    sprint = _sprint(project_id, sprint_id)
    db = get_db()
    items = backlog.list_items(db, project_id, sprint_id=sprint_id, limit=500)
    work = sprints.list_work_items(db, sprint_id)
    candidates = [
        i
        for i in backlog.list_items(db, project_id, status="TRIAGED", limit=200)
        if i.sprint_id is None
    ]
    return render_template(
        "sprints/detail.html",
        project=project,
        sprint=sprint,
        items=items,
        work=work,
        titles={w.id: w.title for w in work},
        docs=sprints.list_document_refs(db, sprint_id),
        readiness=sprints.list_readiness(db, sprint_id),
        approvals=sprints.list_approvals(db, sprint_id),
        candidates=candidates,
        layers=_layers(work),
        planning=sprint.status == "PLANNING",
    )


def _layers(work):
    """Group tasks into dependency layers (column 1 has no dependencies) for
    the task-graph view: columns left-to-right on desktop, stacked on mobile."""
    remaining = {w.id: w for w in work}
    placed: set[int] = set()
    layers = []
    while remaining:
        layer = [w for w in remaining.values() if all(d in placed or d not in remaining for d in w.depends_on)]
        if not layer:  # cycle; should be impossible, but never loop forever
            layer = list(remaining.values())
        layers.append(layer)
        for w in layer:
            placed.add(w.id)
            del remaining[w.id]
    return layers


@bp.post("/<int:sprint_id>/edit")
def edit_sprint(project_id: int, sprint_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    back = _detail_url(project_id, sprint_id)
    fields = {
        k: request.form[k]
        for k in ("name", "goal", "description", "planning_profile", "start_date", "target_date")
        if k in request.form
    }
    for key in ("start_date", "target_date"):
        if key in fields:
            fields[key] = fields[key] or None
    _, failed = _guard(lambda: sprints.update_sprint(get_db(), sprint_id, **fields), back)
    return failed or _reply("Sprint updated", True, back)


@bp.post("/<int:sprint_id>/items")
def add_items(project_id: int, sprint_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    back = _detail_url(project_id, sprint_id)
    ids = [int(v) for v in request.values.getlist("item_ids") if v.isdigit()]
    if not ids:
        return _reply("Select at least one backlog item", False, back)
    added, failed = _guard(lambda: workflow.select_items(get_db(), sprint_id, ids), back)
    return failed or _reply(f"{len(added)} item(s) added", True, back)


@bp.route("/<int:sprint_id>/items/<int:item_id>", methods=["DELETE", "POST"])
def remove_item(project_id: int, sprint_id: int, item_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    back = _detail_url(project_id, sprint_id)
    _, failed = _guard(lambda: workflow.deselect_item(get_db(), sprint_id, item_id), back)
    return failed or _reply("Item removed from sprint", True, back)


@bp.post("/<int:sprint_id>/documents")
def add_document(project_id: int, sprint_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    back = _detail_url(project_id, sprint_id)
    _, failed = _guard(
        lambda: sprints.add_document_ref(
            get_db(), sprint_id, request.form.get("reference", ""), request.form.get("note", "")
        ),
        back,
    )
    return failed or _reply("Document referenced", True, back)


@bp.route("/<int:sprint_id>/documents/<int:ref_id>", methods=["DELETE", "POST"])
def remove_document(project_id: int, sprint_id: int, ref_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    sprints.remove_document_ref(get_db(), sprint_id, ref_id)
    return _reply("Reference removed", True, _detail_url(project_id, sprint_id))


@bp.post("/<int:sprint_id>/plan")
def plan(project_id: int, sprint_id: int):
    project = _project(project_id)
    _sprint(project_id, sprint_id)
    back = _detail_url(project_id, sprint_id)

    def go():
        return workflow.start_planning(
            get_db(), sprint_id, _agent(), _context(project), replan=request.form.get("replan") == "1"
        )

    session_id, failed = _guard(go, back)
    if failed:
        return failed
    # Synchronous adapters (the fake agent) are already finished; poll once so
    # the reviewer lands on the proposal without a second click.
    state, failed = _guard(lambda: workflow.ingest_proposal(get_db(), sprint_id, _agent()), back)
    if failed:
        return failed
    message = "Proposal ready for review" if state == "COMPLETE" else "Planning started"
    return _reply(message, True, back, state=state, session_id=session_id)


@bp.get("/<int:sprint_id>/plan/status")
def plan_status(project_id: int, sprint_id: int):
    """Polled while a planning session runs; ingests the proposal once done."""
    project = _project(project_id)
    sprint = _sprint(project_id, sprint_id)
    if sprint.status != "PLANNING":
        return {"state": "COMPLETE" if sprint.status != "DRAFT" else "IDLE", "status": sprint.status}
    try:
        state = workflow.ingest_proposal(get_db(), sprint_id, _agent())
    except workflow.PlanningFailed as exc:
        return {"state": "FAILED", "error": str(exc), "status": "DRAFT"}
    return {"state": state, "status": sprints.get_sprint(get_db(), sprint_id).status}


@bp.route("/<int:sprint_id>/tasks/<int:work_id>", methods=["POST", "PUT"])
def edit_task(project_id: int, sprint_id: int, work_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    back = _detail_url(project_id, sprint_id)
    work = sprints.get_work_item(get_db(), work_id)
    if work is None or work.sprint_id != sprint_id:
        abort(404)
    form = request.form

    def go():
        db = get_db()
        acceptance = form["acceptance"].splitlines() if "acceptance" in form else None
        sprints.update_work_item(
            db,
            work_id,
            title=form.get("title"),
            description=form.get("description"),
            acceptance=acceptance,
            estimate=form.get("estimate"),
            status=form.get("status"),
        )
        if "depends_on" in form or form.get("set_dependencies"):
            sprints.set_dependencies(
                db, work_id, [int(v) for v in form.getlist("depends_on") if v.isdigit()]
            )
        workflow.check(db, sprint_id)

    _, failed = _guard(go, back)
    return failed or _reply("Task updated", True, back)


@bp.post("/<int:sprint_id>/tasks")
def add_task(project_id: int, sprint_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    back = _detail_url(project_id, sprint_id)
    ids = [int(v) for v in request.form.getlist("backlog_item_ids") if v.isdigit()]

    def go():
        sprints.add_work_item(
            get_db(), sprint_id, request.form.get("title", ""),
            request.form.get("description", ""),
            request.form.get("acceptance", "").splitlines(), request.form.get("estimate", ""), ids,
        )
        workflow.check(get_db(), sprint_id)

    _, failed = _guard(go, back)
    return failed or _reply("Task added", True, back)


@bp.route("/<int:sprint_id>/tasks/<int:work_id>/delete", methods=["POST", "DELETE"])
def delete_task(project_id: int, sprint_id: int, work_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    work = sprints.get_work_item(get_db(), work_id)
    if work is None or work.sprint_id != sprint_id:
        abort(404)
    sprints.delete_work_item(get_db(), work_id)
    workflow.check(get_db(), sprint_id)
    return _reply("Task removed", True, _detail_url(project_id, sprint_id))


@bp.post("/<int:sprint_id>/readiness")
def recheck(project_id: int, sprint_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    workflow.check(get_db(), sprint_id)
    return _reply("Readiness re-checked", True, _detail_url(project_id, sprint_id))


@bp.post("/<int:sprint_id>/approve")
def approve(project_id: int, sprint_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    back = _detail_url(project_id, sprint_id)
    _, failed = _guard(
        lambda: workflow.approve(
            get_db(), sprint_id, request.form.get("approved_by", ""), request.form.get("comment", "")
        ),
        back,
    )
    return failed or _reply("Sprint approved and ready", True, back)


@bp.post("/<int:sprint_id>/revoke")
def revoke(project_id: int, sprint_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    back = _detail_url(project_id, sprint_id)
    _, failed = _guard(
        lambda: workflow.revoke(
            get_db(), sprint_id, request.form.get("approved_by", ""), request.form.get("comment", "")
        ),
        back,
    )
    return failed or _reply("Approval revoked", True, back)


@bp.post("/<int:sprint_id>/cancel")
def cancel(project_id: int, sprint_id: int):
    _project(project_id)
    _sprint(project_id, sprint_id)
    back = _detail_url(project_id, sprint_id)
    _, failed = _guard(lambda: sprints.set_status(get_db(), sprint_id, "CANCELLED"), back)
    return failed or _reply("Sprint cancelled", True, back)
