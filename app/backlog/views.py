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

from app.agents import models as agent_models
from app.backlog import attachments, design, discussion, persistence, research
from app.backlog.models import (
    BACKLOG_PRIORITIES,
    BACKLOG_STATUSES,
    TRANSITIONS,
    InvalidTransitionError,
)
from app.db import get_db
from app.sprints import persistence as sprint_persistence
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
        sprints_by_id={s.id: s for s in sprint_persistence.list_sprints(get_db(), project_id)},
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
        research_history=research.item_research_history(db, item_id),
        design_history=design.item_design_history(db, item_id),
        next_statuses=TRANSITIONS[item.status],
        priorities=BACKLOG_PRIORITIES,
        discussions=[(s, agent_models.session_title(db, s)) for s in discussion.discussions_for_item(db, item_id)],
        sprint_choices=_sprint_choices(db, project_id, item),
        current_sprint=sprint_persistence.get_sprint(db, item.sprint_id) if item.sprint_id else None,
    )


def _sprint_choices(db, project_id: int, item) -> list[dict]:
    """Sprints this item could join, each marked editable or not with the
    reason (task 59); only unassigned INBOX/TRIAGED items can join one."""
    from app.sprints import workflow

    if item.sprint_id or item.status not in ("INBOX", "TRIAGED"):
        return []
    return [
        {"sprint": s, "editable": workflow.is_sprint_editable(s), "reason": workflow.membership_closed_message(s)}
        for s in sprint_persistence.list_sprints(db, project_id) if s.status not in ("COMPLETE", "CANCELLED")
    ]


DISCUSSION_AGENTS = ("codex", "claude", "fake")


@bp.post("/items/<int:item_id>/chat")
def discuss_item(project_id: int, item_id: int):
    """Start an interactive session about this item (task 58), preloaded with
    its text, attachments and triage history. Several discussions per item are
    fine; each is listed on the item page."""
    from app.sessions import starter

    project = _project(project_id)
    item = _item(project_id, item_id)
    back = url_for("backlog.view_item", project_id=project_id, item_id=item_id)
    if not project.repositories:
        return _reply("Add a repository to the project before starting a discussion", False, back)
    agent_type = request.form.get("agent_type", "codex").lower()
    if agent_type not in DISCUSSION_AGENTS:
        return _reply("Choose Codex, Claude or Fake", False, back)
    repo = next((r for r in project.repositories if r.is_primary), project.repositories[0])
    db = get_db()
    try:
        session_id = starter.start_interactive_session(
            current_app.config, db, project_id, repo.path, agent_type,
            model=request.form.get("model", "").strip() or None,
            mcp_tools=request.form.get("mcp_tools") == "1",
            extra_context=discussion.item_context(db, item),
            extra_metadata={"backlog_item_id": item_id, "title": f"Discuss backlog #{item_id}: {item.title or item.text[:40]}"[:80]},
        )
    except Exception as exc:  # noqa: BLE001 -- adapter/CLI failures are user-facing
        return _reply(f"Could not start the discussion: {exc}", False, back, 502)
    persistence.record_note(db, item_id, f"{discussion.SOURCE}: chat session #{session_id} started", _actor())
    url = url_for("sessions.view_session", session_id=session_id)
    if _wants_json():
        return {"status": "success", "message": "Discussion started", "session_id": session_id, "redirect": url}, 200
    return redirect(url)


@bp.post("/items/<int:item_id>/chat/<int:session_id>/apply")
def apply_discussion(project_id: int, item_id: int, session_id: int):
    """Apply the selected agent replies of an item discussion to the item --
    the explicit, person-confirmed step; nothing changes the item otherwise."""
    _project(project_id)
    item = _item(project_id, item_id)
    db = get_db()
    back = url_for("sessions.view_session", session_id=session_id)
    session = agent_models.get_agent_session(db, session_id)
    if session is None or discussion.session_item_id(session) != item_id:
        return _reply("That session is not a discussion of this item", False, back, 404)
    try:
        event_ids = [int(v) for v in request.form.getlist("event_id")]
        text = discussion.proposal_text(db, session_id, event_ids)
        status = discussion.apply_to_item(db, item, session_id, text, request.form.get("mode", "replace"))
    except (ValueError, InvalidTransitionError) as exc:
        return _reply(str(exc), False, back)
    return _reply(f"Backlog #{item_id} updated ({status.lower()})", True, back, item_status=status)


@bp.post("/items/<int:item_id>/research")
def research_item(project_id: int, item_id: int):
    """Ask a RESEARCH-role agent about this item (§50, task 44). Blocks for the
    research pass -- see app/backlog/research.py's module docstring for why
    that matches this app's existing sprint-planning precedent -- so the
    trigger button is a `data-ajax-reload` (disabled while in flight, page
    reload on completion shows the new report), the same control Ralph's
    research-on-failure report already uses (app/templates/ralph/detail.html)."""
    project = _project(project_id)
    item = _item(project_id, item_id)
    db = get_db()
    back = url_for("backlog.view_item", project_id=project_id, item_id=item_id)
    try:
        result = research.run_item_research(current_app.config, db, _root(), project, item)
    except (ValueError, RuntimeError) as exc:
        return _reply(f"Research could not start: {exc}", False, back, 502)

    outcome = result.outcome
    if outcome.state == "COMPLETE":
        return _reply(
            "Research complete", True, back,
            link_id=result.link_id, research_session_id=result.research_session_id, outcome=outcome.state,
            report=outcome.report.to_dict(),
        )
    message = outcome.error or f"Research ended {outcome.state.lower()}"
    return _reply(message, False, back, 502)


def _research_link(project_id: int, item_id: int, link_id: int):
    _item(project_id, item_id)
    link = persistence.get_research_link(get_db(), link_id)
    if link is None or link.backlog_item_id != item_id:
        abort(404)
    return link


@bp.post("/items/<int:item_id>/research/<int:link_id>/accept")
def accept_research(project_id: int, item_id: int, link_id: int):
    """Advisory-only acceptance (docs/AGENT_ADAPTER.md §23: "A person accepts
    findings before they change a plan or acceptance criteria"): appends the
    report summary to the item's text and logs a triage-history note, mirroring
    Ralph's `use_research` (app/ralph/views.py) for the same report shape."""
    _project(project_id)
    item = _item(project_id, item_id)
    db = get_db()
    back = url_for("backlog.view_item", project_id=project_id, item_id=item_id)
    link = _research_link(project_id, item_id, link_id)
    if link.status != "PENDING":
        return _reply("This report was already reviewed", False, back, 409)
    session_row = agent_models.get_research_session(db, link.research_session_id)
    if session_row is None or session_row.status != "COMPLETED" or not session_row.findings_summary.strip():
        return _reply("There is no completed research summary to accept", False, back, 409)
    block = f"Research summary ({session_row.completed_at or 'n/a'}):\n{session_row.findings_summary}".strip()
    text = f"{item.text}\n\n{block}".strip() if item.text.strip() else block
    persistence.update_item(db, item_id, text=text)
    persistence.record_note(db, item_id, f"Accepted research summary (session #{link.research_session_id})", changed_by=_actor())
    persistence.set_research_link_status(db, link_id, "ACCEPTED")
    return _reply("Research summary added to the item", True, back, text=text)


@bp.post("/items/<int:item_id>/research/<int:link_id>/dismiss")
def dismiss_research(project_id: int, item_id: int, link_id: int):
    """Marks the report reviewed without changing the item -- mirrors Ralph's
    `dismiss_research`."""
    _project(project_id)
    _item(project_id, item_id)
    db = get_db()
    back = url_for("backlog.view_item", project_id=project_id, item_id=item_id)
    link = _research_link(project_id, item_id, link_id)
    if link.status != "PENDING":
        return _reply("This report was already reviewed", False, back, 409)
    persistence.set_research_link_status(db, link_id, "DISMISSED")
    return _reply("Dismissed", True, back)


@bp.post("/items/<int:item_id>/design")
def design_item(project_id: int, item_id: int):
    """Ask a DESIGN-role agent to propose tests for this item (§51). Blocks
    for the design pass, same reasoning as `research_item`, so the trigger
    button is a `data-ajax-reload` too."""
    project = _project(project_id)
    item = _item(project_id, item_id)
    db = get_db()
    back = url_for("backlog.view_item", project_id=project_id, item_id=item_id)
    try:
        result = design.run_item_design(current_app.config, db, project, item)
    except (ValueError, RuntimeError) as exc:
        return _reply(f"Design could not start: {exc}", False, back, 502)

    outcome = result.outcome
    if outcome.state == "COMPLETE":
        return _reply(
            "Design complete", True, back,
            design_session_id=result.design_session_id, proposal_ids=result.proposal_ids,
            proposals=[p.__dict__ for p in outcome.proposals],
        )
    message = outcome.error or f"Design ended {outcome.state.lower()}"
    return _reply(message, False, back, 502)


def _test_proposal(project_id: int, item_id: int, proposal_id: int):
    _item(project_id, item_id)
    proposal = persistence.get_test_proposal(get_db(), proposal_id)
    if proposal is None or proposal.backlog_item_id != item_id:
        abort(404)
    return proposal


@bp.post("/items/<int:item_id>/design/<int:proposal_id>/accept")
def accept_test_proposal(project_id: int, item_id: int, proposal_id: int):
    """Advisory-only acceptance, same rule as Research (§50): nothing becomes
    a real acceptance criterion until a Sprint containing this item is
    approved (`app.acceptance.service.sync_from_work_items`)."""
    _project(project_id)
    db = get_db()
    back = url_for("backlog.view_item", project_id=project_id, item_id=item_id)
    proposal = _test_proposal(project_id, item_id, proposal_id)
    if proposal.status != "PENDING":
        return _reply("This proposal was already reviewed", False, back, 409)
    persistence.set_test_proposal_status(db, proposal_id, "ACCEPTED")
    persistence.record_note(db, item_id, f"Accepted test proposal: {proposal.title}", changed_by=_actor())
    return _reply("Accepted", True, back)


@bp.post("/items/<int:item_id>/design/<int:proposal_id>/dismiss")
def dismiss_test_proposal(project_id: int, item_id: int, proposal_id: int):
    _project(project_id)
    db = get_db()
    back = url_for("backlog.view_item", project_id=project_id, item_id=item_id)
    proposal = _test_proposal(project_id, item_id, proposal_id)
    if proposal.status != "PENDING":
        return _reply("This proposal was already reviewed", False, back, 409)
    persistence.set_test_proposal_status(db, proposal_id, "DISMISSED")
    return _reply("Dismissed", True, back)


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
