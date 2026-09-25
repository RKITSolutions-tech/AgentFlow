from __future__ import annotations

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from app.db import get_db
from app.projects import clone, models
from app.security import PathNotAllowedError

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
    return render_template("projects/detail.html", project=project, roots=_roots())


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

        models.update_project(db, project_id, name, description)
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
                if session.agent_type.lower() == "codex":
                    adapter = CodexAdapter(db=db, execution_provider=execution_provider)
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
