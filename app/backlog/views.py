from __future__ import annotations

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

from app.backlog import attachments, persistence
from app.backlog.models import (
    BACKLOG_PRIORITIES,
    BACKLOG_STATUSES,
    TRANSITIONS,
    InvalidTransitionError,
)
from app.db import get_db
from app.projects import models as project_models
from app.runs import artifacts as run_artifacts

bp = Blueprint("backlog", __name__, url_prefix="/projects/<int:project_id>/backlog")

PAGE_SIZE = 25
MAX_FILES = 10
# Statuses the inbox page can filter by; everything past SELECTED belongs to
# sprint planning and is shown on the sprint pages instead.
INBOX_FILTERS = ("INBOX", "TRIAGED", "SELECTED", "ARCHIVED", "REJECTED")
# Statuses an item can be "in a sprint" under, before sprints exist as records.
SPRINT_STATUSES = ("SELECTED", "PLANNING", "PLANNED", "READY", "RELEASED")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _project(project_id: int):
    project = project_models.get_project(get_db(), project_id)
    if project is None:
        abort(404)
    return project


def _item(project_id: int, item_id: int):
    item = persistence.get_item(get_db(), item_id)
    if item is None or item.project_id != project_id:
        abort(404)
    return item


def _root() -> str:
    return run_artifacts.artifact_root(current_app.config)


def _reply(message: str, ok: bool, target: str, code: int = 400, **extra):
    """JSON for AJAX callers, flash + redirect to `target` otherwise."""
    if _wants_json():
        if ok:
            return {"status": "success", "message": message, **extra}, 200
        return {"error": message}, code
    flash(message, "success" if ok else "error")
    return redirect(target)


def _counts(project_id: int) -> dict[str, int]:
    rows = get_db().execute(
        "SELECT status, COUNT(*) FROM backlog_items WHERE project_id = ? GROUP BY status",
        (project_id,),
    ).fetchall()
    return {status: n for status, n in rows}


def _with_attachments(items):
    db = get_db()
    return {i.id: persistence.list_attachments(db, i.id) for i in items}


def _actor() -> str:
    return "user"


@bp.get("")
def index(project_id: int):
    return redirect(url_for("backlog.inbox", project_id=project_id))


@bp.get("/inbox")
def inbox(project_id: int):
    project = _project(project_id)
    db = get_db()
    status = request.args.get("status", "INBOX")
    if status not in INBOX_FILTERS:
        status = "INBOX"
    page = max(request.args.get("page", 1, type=int), 1)
    counts = _counts(project_id)
    items = persistence.list_items(
        db, project_id, status=status, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    return render_template(
        "backlog/inbox.html",
        project=project,
        items=items,
        files=_with_attachments(items),
        status=status,
        filters=INBOX_FILTERS,
        counts=counts,
        page=page,
        pages=max((counts.get(status, 0) + PAGE_SIZE - 1) // PAGE_SIZE, 1),
        priorities=BACKLOG_PRIORITIES,
    )


@bp.get("/triage")
def triage(project_id: int):
    project = _project(project_id)
    items = persistence.list_items(get_db(), project_id, status="INBOX", limit=100)
    return render_template(
        "backlog/triage.html",
        project=project,
        items=items,
        files=_with_attachments(items),
        counts=_counts(project_id),
        priorities=BACKLOG_PRIORITIES,
    )


@bp.get("/sprint")
def sprint_items(project_id: int):
    project = _project(project_id)
    db = get_db()
    items = [
        i
        for status in SPRINT_STATUSES
        for i in persistence.list_items(db, project_id, status=status, limit=200)
    ]
    ready = persistence.list_items(db, project_id, status="TRIAGED", limit=100)
    return render_template(
        "backlog/sprint_items.html",
        project=project,
        items=items,
        candidates=ready,
        counts=_counts(project_id),
    )


@bp.post("/items")
def create_item(project_id: int):
    _project(project_id)
    back = url_for("backlog.inbox", project_id=project_id)
    text = request.form.get("text", "").strip()
    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not text and not uploads:
        return _reply("Enter some text or attach a file", False, back)
    if len(uploads) > MAX_FILES:
        return _reply(f"Attach at most {MAX_FILES} files", False, back)
    try:
        item_id = persistence.create_item(
            get_db(),
            project_id,
            text=text,
            title=request.form.get("title", "").strip()[:120],
            priority=request.form.get("priority") or None,
            created_by=_actor(),
            source_type="manual",
            source_reference=request.form.get("source", "").strip()[:500],
        )
    except ValueError as exc:
        return _reply(str(exc), False, back)

    skipped = []
    for upload in uploads:
        try:
            attachments.store_attachment(
                get_db(), _root(), project_id, item_id, upload.filename, upload.read()
            )
        except (ValueError, run_artifacts.ArtifactPathError) as exc:
            skipped.append(f"{upload.filename}: {exc}")
    message = "Item added"
    if skipped:
        message += " (not stored: " + "; ".join(skipped) + ")"
    return _reply(message, True, back, item_id=item_id)


@bp.get("/items/<int:item_id>")
def view_item(project_id: int, item_id: int):
    project = _project(project_id)
    item = _item(project_id, item_id)
    db = get_db()
    return render_template(
        "backlog/item.html",
        project=project,
        item=item,
        files=persistence.list_attachments(db, item_id),
        history=persistence.list_history(db, item_id),
        next_statuses=TRANSITIONS[item.status],
        priorities=BACKLOG_PRIORITIES,
    )


def _apply_update(project_id: int, item_id: int, values) -> tuple[str, bool, int]:
    """Apply title/text/priority edits and an optional status change."""
    item = _item(project_id, item_id)
    db = get_db()
    fields = {}
    for name in ("title", "text"):
        if name in values:
            fields[name] = values[name].strip()
    if "priority" in values:
        fields["priority"] = values["priority"]
    try:
        persistence.update_item(db, item_id, **fields)
        status = values.get("status", "")
        if status and status != item.status:
            persistence.transition(
                db, item_id, status, notes=values.get("notes", "").strip(), changed_by=_actor()
            )
    except InvalidTransitionError as exc:
        return str(exc), False, 409
    except ValueError as exc:
        return str(exc), False, 400
    return "Item updated", True, 200


@bp.route("/items/<int:item_id>", methods=["POST", "PUT", "PATCH"])
def update_item(project_id: int, item_id: int):
    _project(project_id)
    values = request.form if request.form else (request.get_json(silent=True) or {})
    if not values:
        values = request.args
    message, ok, code = _apply_update(project_id, item_id, values)
    back = url_for("backlog.view_item", project_id=project_id, item_id=item_id)
    item = persistence.get_item(get_db(), item_id)
    return _reply(message, ok, back, code, status_value=item.status, priority=item.priority)


@bp.route("/items/<int:item_id>", methods=["DELETE"])
@bp.post("/items/<int:item_id>/archive")
def archive_item(project_id: int, item_id: int):
    """Archive rather than delete: history and attachments stay traceable."""
    _project(project_id)
    back = url_for("backlog.inbox", project_id=project_id)
    _, ok, code = _apply_update(project_id, item_id, {"status": "ARCHIVED"})
    message = "Item archived" if ok else "This item cannot be archived from its current status"
    return _reply(message, ok, back, code)


@bp.post("/bulk")
def bulk(project_id: int):
    """Move several items to one status; each is checked against TRANSITIONS."""
    _project(project_id)
    back = url_for("backlog.triage", project_id=project_id)
    status = request.values.get("status", "")
    if status not in BACKLOG_STATUSES:
        return _reply("Choose a status", False, back)
    ids = request.values.getlist("ids")
    if not ids:
        return _reply("Select at least one item", False, back)
    changed, failed = [], []
    for raw in ids:
        try:
            item_id = int(raw)
        except ValueError:
            continue
        message, ok, _ = _apply_update(project_id, item_id, {"status": status})
        (changed if ok else failed).append(item_id)
    summary = f"{len(changed)} item(s) moved to {status.title()}"
    if failed:
        summary += f"; {len(failed)} could not move"
    return _reply(summary, bool(changed), back, 409, changed=changed, failed=failed)


@bp.get("/items/<int:item_id>/attachments/<int:attachment_id>")
def download_attachment(project_id: int, item_id: int, attachment_id: int):
    _item(project_id, item_id)
    attachment = persistence.get_attachment(get_db(), item_id, attachment_id)
    if attachment is None:
        abort(404)
    try:
        path = attachments.attachment_file(_root(), attachment)
    except run_artifacts.ArtifactPathError:
        abort(404)
    inline = attachment.kind == "IMAGE" and not attachment.name.lower().endswith(".svg")
    return send_file(path, as_attachment=not inline, download_name=attachment.name)
