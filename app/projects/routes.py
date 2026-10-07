from __future__ import annotations

from flask import Blueprint, Response, current_app, flash, jsonify, redirect, render_template, request, url_for

from app.agents import models as agent_models
from app.db import get_db
from app.projects import clone, models
from app.security import PathNotAllowedError
from app.sessions import chat
from app.sessions.export import export_chat_history

bp = Blueprint("projects", __name__, url_prefix="/projects")


def _roots() -> list[str]:
    return list(current_app.config["ALLOWED_PROJECT_ROOTS"])


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@bp.get("")
def list_projects():
    db = get_db()
    show_archived = request.args.get("archived") == "1"
    projects = models.list_projects(db, archived=show_archived)
    return render_template("projects/list.html", projects=projects, show_archived=show_archived)


@bp.route("/new", methods=["GET", "POST"])
def new_project():
    if request.method == "POST":
        name = request.form["name"].strip()
        description = request.form.get("description", "").strip()
        if not name:
            flash("Project name is required.", "error")
            return render_template("projects/new.html", roots=_roots()), 400

        db = get_db()
        project_id = models.create_project(db, name, description)

        repo_name = request.form.get("repo_name", "").strip()
        repo_path = request.form.get("repo_path", "").strip()

        if repo_path:
            if not repo_name:
                flash("Repository name is required when providing a path.", "error")
                return render_template("projects/new.html", roots=_roots()), 400

            try:
                models.add_repository(
                    db,
                    project_id,
                    repo_name,
                    repo_path,
                    current_app.config["ALLOWED_PROJECT_ROOTS"],
                    is_primary=True,
                )
            except PathNotAllowedError as exc:
                flash(str(exc), "error")

        return redirect(url_for("projects.view_project", project_id=project_id))

    return render_template(
        "projects/new.html", roots=_roots()
    )


@bp.get("/<int:project_id>")
def view_project(project_id: int):
    db = get_db()
    project = models.get_project(db, project_id)
    if project is None:
        return render_template("404.html"), 404
    recent_chat = chat.get_recent_messages(db, project_id)
    return render_template(
        "projects/detail.html", project=project, roots=_roots(), recent_chat=recent_chat
    )


@bp.get("/<int:project_id>/chat-history")
def chat_history(project_id: int):
    db = get_db()
    project = models.get_project(db, project_id)
    if project is None:
        return render_template("404.html"), 404

    query = request.args.get("q", "").strip()
    role = request.args.get("role") or None
    session_filter = request.args.get("session_id", type=int)
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None

    # With no explicit filter, default to the last 24h (design doc §5.2.2 "Recent");
    # an explicit date range, session, role or search broadens the view deliberately.
    showing_recent_default = not (query or start_date or end_date or session_filter or role)

    if query:
        messages = chat.search_messages(db, project_id, query)
    else:
        effective_start = start_date
        if showing_recent_default:
            effective_start = db.execute("SELECT datetime('now', '-24 hours') AS t").fetchone()["t"]
        messages = chat.get_chat_history(
            db, project_id, role=role, session_id=session_filter,
            start_date=effective_start, end_date=f"{end_date} 23:59:59" if end_date else None,
        )
    sessions = [
        s for s in agent_models.list_agent_sessions_for_project(db, project_id)
        if s.role == "GENERAL"
    ]
    summaries = chat.list_summaries_for_project(db, project_id)
    return render_template(
        "projects/chat_history.html",
        project=project, messages=messages, sessions=sessions, summaries=summaries,
        query=query, role=role, session_filter=session_filter,
        start_date=start_date, end_date=end_date, showing_recent_default=showing_recent_default,
    )


@bp.get("/<int:project_id>/chat/export")
def chat_export(project_id: int):
    db = get_db()
    project = models.get_project(db, project_id)
    if project is None:
        return render_template("404.html"), 404

    export_format = request.args.get("format", "markdown")
    if export_format not in ("markdown", "json"):
        return jsonify({"error": "format must be 'markdown' or 'json'"}), 400
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None

    content = export_chat_history(
        db, project_id, format=export_format, start_date=start_date,
        end_date=f"{end_date} 23:59:59" if end_date else None,
    )
    mimetype = "application/json" if export_format == "json" else "text/markdown"
    extension = "json" if export_format == "json" else "md"
    filename = f"{project.slug}-chat.{extension}"
    return Response(
        content, mimetype=mimetype,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.get("/<int:project_id>/chat/archive/<int:summary_id>")
def chat_archive_expand(project_id: int, summary_id: int):
    db = get_db()
    summary = chat.get_summary(db, summary_id)
    if summary is None or summary.project_id != project_id:
        return jsonify({"error": "Summary not found"}), 404
    messages = chat.get_chat_history(
        db, project_id, start_date=summary.period_start, end_date=summary.period_end,
    )
    return jsonify({
        "messages": [
            {
                "created_at": m.created_at, "role": m.role, "content": m.content,
                "message_type": m.message_type, "redacted": m.redacted,
            }
            for m in messages
        ]
    })


@bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
def edit_project(project_id: int):
    db = get_db()
    project = models.get_project(db, project_id)
    if project is None:
        return render_template("404.html"), 404

    if request.method == "POST":
        name = request.form["name"].strip()
        description = request.form.get("description", "").strip()
        if not name:
            flash("Project name is required.", "error")
            return render_template("projects/edit.html", project=project), 400

        try:
            models.update_project(db, project_id, name, description, request.form.get("lock_scope") or None)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("projects/edit.html", project=project), 400
        return redirect(url_for("projects.view_project", project_id=project_id))

    return render_template("projects/edit.html", project=project)


@bp.post("/<int:project_id>/repositories")
def add_repository(project_id: int):
    db = get_db()
    project = models.get_project(db, project_id)
    if project is None:
        return render_template("404.html"), 404

    name = request.form["name"].strip()
    path = request.form["path"].strip()
    is_primary = bool(request.form.get("is_primary"))

    try:
        models.add_repository(
            db,
            project_id,
            name,
            path,
            current_app.config["ALLOWED_PROJECT_ROOTS"],
            is_primary,
        )
    except PathNotAllowedError as exc:
        flash(str(exc), "error")

    return redirect(url_for("projects.view_project", project_id=project_id))


@bp.post("/<int:project_id>/delete")
def delete_project(project_id: int):
    from app.agents.claude import ClaudeAdapter
    from app.agents.codex import CodexAdapter
    from app.agents.fake import FakeAgentAdapter
    from app.agents.models import list_agent_sessions_for_project
    from app.execution.host import HostExecutionProvider

    db = get_db()
    project = models.get_project(db, project_id)
    if project is None:
        if _wants_json():
            return jsonify({"error": "Project not found"}), 404
        return redirect(url_for("projects.list_projects"))

    running_sessions = [
        s for s in list_agent_sessions_for_project(db, project_id)
        if s.status in ("RUNNING", "STARTING")
    ]
    if running_sessions:
        execution_provider = HostExecutionProvider(
            current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
        )
        for session in running_sessions:
            try:
                agent_type = session.agent_type.lower()
                if agent_type == "codex":
                    adapter = CodexAdapter(db=db, execution_provider=execution_provider)
                elif agent_type == "claude":
                    adapter = ClaudeAdapter(db=db, execution_provider=execution_provider)
                else:
                    adapter = FakeAgentAdapter(db=db)
                adapter.stop(session.id)
            except Exception:
                pass

    models.delete_project(db, project_id)

    if _wants_json():
        return jsonify({"status": "deleted", "message": f"Removed {project.name}."}), 200

    flash(f"Removed {project.name}.", "info")
    return redirect(url_for("projects.list_projects"))


def _project_flag_action(project_id: int, apply, message: str):
    """Star/archive style toggles: JSON for AJAX callers, else flash + redirect."""
    db = get_db()
    project = models.get_project(db, project_id)
    if project is None:
        if _wants_json():
            return jsonify({"error": "Project not found"}), 404
        return render_template("404.html"), 404
    apply(db, project_id)
    if _wants_json():
        return jsonify({"status": "ok", "message": message.format(name=project.name)}), 200
    flash(message.format(name=project.name), "info")
    return redirect(request.referrer or url_for("projects.list_projects"))


@bp.post("/<int:project_id>/star")
def star_project(project_id: int):
    return _project_flag_action(
        project_id, lambda db, pid: models.set_starred(db, pid, True), "Starred {name}."
    )


@bp.post("/<int:project_id>/unstar")
def unstar_project(project_id: int):
    return _project_flag_action(
        project_id, lambda db, pid: models.set_starred(db, pid, False), "Unstarred {name}."
    )


@bp.post("/<int:project_id>/archive")
def archive_project(project_id: int):
    return _project_flag_action(
        project_id, lambda db, pid: models.set_archived(db, pid, True), "Archived {name}."
    )


@bp.post("/<int:project_id>/restore")
def restore_project(project_id: int):
    return _project_flag_action(
        project_id, lambda db, pid: models.set_archived(db, pid, False), "Restored {name}."
    )


def _provider():
    from app.execution.host import HostExecutionProvider

    return HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )


@bp.get("/clone")
def clone_page():
    roots = current_app.config["ALLOWED_PROJECT_ROOTS"]
    return render_template(
        "projects/clone.html", default_parent=roots[0] if roots else "", roots=list(roots)
    )


@bp.post("/clone")
def start_clone():
    try:
        job = clone.start_clone(
            get_db(),
            _provider(),
            current_app.config["ALLOWED_PROJECT_ROOTS"],
            url=request.form.get("url", ""),
            parent=request.form.get("parent", ""),
            folder=request.form.get("folder", ""),
            project_name=request.form.get("name", ""),
            description=request.form.get("description", ""),
        )
    except clone.CloneError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_job_payload(job, None)), 202


def _job_payload(job, percent):
    return {
        "id": job.id,
        "status": job.status,
        "message": job.message,
        "percent": percent,
        "project_url": url_for("projects.view_project", project_id=job.project_id)
        if job.project_id
        else None,
    }


@bp.get("/clone/<int:job_id>")
def clone_status(job_id: int):
    job, percent = clone.poll_job(
        get_db(), _provider(), current_app.config["ALLOWED_PROJECT_ROOTS"], job_id
    )
    if job is None:
        return jsonify({"error": "Clone not found"}), 404
    return jsonify(_job_payload(job, percent))


@bp.post("/clone/<int:job_id>/cancel")
def clone_cancel(job_id: int):
    job = clone.cancel_job(
        get_db(), _provider(), current_app.config["ALLOWED_PROJECT_ROOTS"], job_id
    )
    if job is None:
        return jsonify({"error": "Clone not found"}), 404
    return jsonify(_job_payload(job, None))
