from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.acceptance import models, service, templates
from app.acceptance.models import AcceptanceError
from app.artifacts import models as artifact_models
from app.db import get_db
from app.projects import models as project_models

bp = Blueprint("acceptance", __name__, url_prefix="/projects/<int:project_id>/acceptance")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _project(project_id: int):
    project = project_models.get_project(get_db(), project_id)
    if project is None:
        abort(404)
    return project


def _criterion(project_id: int, criterion_id: int) -> models.Criterion:
    c = models.get(get_db(), criterion_id)
    if c is None or c.project_id != project_id:
        abort(404)
    return c


def _reply(message: str, ok: bool, target: str, code: int = 400, **extra):
    if _wants_json():
        if ok:
            return {"status": "success", "message": message, **extra}, 200
        return {"error": message}, code
    flash(message, "success" if ok else "error")
    return redirect(target)


def _detail(project_id: int, criterion_id: int) -> str:
    return url_for("acceptance.view_criterion", project_id=project_id, criterion_id=criterion_id)


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _run(project_id: int, criterion_id: int, fn, message: str, code: int = 409, **extra):
    """Apply a workflow change to a criterion, mapping domain errors to a reply."""
    _project(project_id)
    _criterion(project_id, criterion_id)
    try:
        fn()
    except AcceptanceError as exc:
        return _reply(str(exc), False, _detail(project_id, criterion_id), code)
    return _reply(message, True, _detail(project_id, criterion_id), **extra)


@bp.get("")
def list_criteria(project_id: int):
    project = _project(project_id)
    status = request.args.get("status")
    criteria = models.list_criteria(
        get_db(), project_id, _int(request.args.get("work_item")), _int(request.args.get("run")),
        status if status in models.STATUSES else None,
    )
    return render_template(
        "acceptance/list.html", project=project, criteria=criteria, statuses=models.STATUSES,
        status=status or "", templates=templates.list_templates(),
        work_item=request.args.get("work_item", ""), run=request.args.get("run", ""),
    )


@bp.get("/templates.json")
def templates_json(project_id: int):
    _project(project_id)
    return {"templates": templates.list_templates()}


@bp.post("")
def create_criterion(project_id: int):
    _project(project_id)
    back = url_for("acceptance.list_criteria", project_id=project_id)
    db = get_db()
    work_item, run = _int(request.form.get("work_item_id")), _int(request.form.get("ralph_run_id"))
    owner = {"work_item_id": work_item, "ralph_run_id": run}
    if work_item and not db.execute(
        "SELECT 1 FROM planned_work_items w JOIN sprints s ON s.id = w.sprint_id WHERE w.id = ? AND s.project_id = ?",
        (work_item, project_id),
    ).fetchone():
        return _reply("That planned task is not in this project", False, back)
    if run and not db.execute("SELECT 1 FROM ralph_runs WHERE id = ? AND project_id = ?", (run, project_id)).fetchone():
        return _reply("That Ralph run is not in this project", False, back)
    author = request.form.get("author", "").strip()
    required = request.form.get("required", "on") == "on"
    try:
        template = request.form.get("template", "")
        if template:
            values = {k[6:]: v for k, v in request.form.items() if k.startswith("field_")}
            criterion_id = service.create_from_template(db, project_id, template, values, author, required=required, **owner)
        else:
            criterion_id = models.create(
                db, project_id, request.form.get("title", ""), request.form.get("description", ""),
                required=required, created_by=author, **owner,
            )
    except AcceptanceError as exc:
        return _reply(str(exc), False, back)
    return _reply("Criterion added", True, _detail(project_id, criterion_id), criterion_id=criterion_id,
                  redirect=_detail(project_id, criterion_id))


@bp.get("/<int:criterion_id>")
def view_criterion(project_id: int, criterion_id: int):
    project = _project(project_id)
    c = _criterion(project_id, criterion_id)
    db = get_db()
    evidence = models.list_evidence(db, criterion_id)
    described = []
    for e in evidence:
        link = None
        if e.evidence_type == "ARTIFACT":
            a = artifact_models.get_artifact(db, e.reference_id)
            if a:
                link = (a.name, url_for("artifacts.detail", project_id=project_id, artifact_id=a.id))
        elif e.evidence_type == "TEST_RESULT":
            row = db.execute("SELECT execution_id, element_name FROM step_executions WHERE id = ?", (e.reference_id,)).fetchone()
            if row:
                link = (f"{row['element_name']} (run #{row['execution_id']})",
                        url_for("pipelines.view_execution", project_id=project_id, execution_id=row["execution_id"]))
        described.append((e, link))
    return render_template("acceptance/detail.html", project=project, c=c, evidence=described)


@bp.post("/<int:criterion_id>")
def update_criterion(project_id: int, criterion_id: int):
    return _run(
        project_id, criterion_id,
        lambda: models.update_text(
            get_db(), criterion_id, request.form.get("title", ""), request.form.get("description", ""),
            request.form.get("required", "") == "on",
        ),
        "Criterion updated",
    )


@bp.post("/<int:criterion_id>/delete")
def delete_criterion(project_id: int, criterion_id: int):
    _project(project_id)
    _criterion(project_id, criterion_id)
    back = url_for("acceptance.list_criteria", project_id=project_id)
    try:
        models.delete_draft(get_db(), criterion_id)
    except AcceptanceError as exc:
        return _reply(str(exc), False, back, 409)
    return _reply("Criterion deleted", True, back, redirect=back)


def _workflow(name: str, message: str):
    def view(project_id: int, criterion_id: int):
        form = request.form
        actions = {
            "approve": lambda: service.approve(get_db(), criterion_id, form.get("by", "")),
            "waive": lambda: service.waive(get_db(), criterion_id, form.get("by", ""), form.get("reason", "")),
            "verify": lambda: service.verify(get_db(), criterion_id, form.get("by", ""), form.get("note", "")),
            "fail": lambda: service.fail(get_db(), criterion_id, form.get("by", ""), form.get("note", "")),
            "reopen": lambda: service.reopen(get_db(), criterion_id, form.get("by", "")),
        }
        return _run(project_id, criterion_id, actions[name], message)

    view.__name__ = name
    return view


for _name, _message in (
    ("approve", "Criterion approved"), ("waive", "Criterion waived"), ("verify", "Criterion verified"),
    ("fail", "Criterion marked failed"), ("reopen", "Criterion re-opened"),
):
    bp.add_url_rule(f"/<int:criterion_id>/{_name}", _name, _workflow(_name, _message), methods=["POST"])


@bp.post("/<int:criterion_id>/suggest")
def suggest(project_id: int, criterion_id: int):
    made = []
    return _run(
        project_id, criterion_id,
        lambda: made.extend(service.suggest_evidence(get_db(), criterion_id)),
        "Suggestions refreshed", suggestions=len(made),
    )


@bp.post("/<int:criterion_id>/evidence")
def add_evidence(project_id: int, criterion_id: int):
    form = request.form
    return _run(
        project_id, criterion_id,
        lambda: service.link_evidence(
            get_db(), criterion_id, form.get("type", "MANUAL"), _int(form.get("reference_id")),
            form.get("note", ""), form.get("by", ""),
        ),
        "Evidence linked",
    )


@bp.post("/<int:criterion_id>/evidence/<int:evidence_id>/<action>")
def review_evidence(project_id: int, criterion_id: int, evidence_id: int, action: str):
    if action not in ("accept", "dismiss"):
        abort(404)
    ev = models.get_evidence(get_db(), evidence_id)
    if ev is None or ev.criterion_id != criterion_id:
        abort(404)
    fn = service.accept_evidence if action == "accept" else service.dismiss_evidence
    return _run(
        project_id, criterion_id, lambda: fn(get_db(), evidence_id, request.form.get("by", "")),
        "Evidence linked" if action == "accept" else "Suggestion dismissed",
    )
