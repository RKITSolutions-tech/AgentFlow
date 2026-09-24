from __future__ import annotations

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from app.db import get_db
from app.projects import models
from app.security import PathNotAllowedError

bp = Blueprint("projects", __name__, url_prefix="/projects")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@bp.get("")
def list_projects():
    db = get_db()
    projects = models.list_projects(db)
    return render_template("projects/list.html", projects=projects)


@bp.route("/new", methods=["GET", "POST"])
def new_project():
    if request.method == "POST":
        name = request.form["name"].strip()
        description = request.form.get("description", "").strip()
        if not name:
            flash("Project name is required.", "error")
            return render_template("projects/new.html"), 400

        db = get_db()
        project_id = models.create_project(db, name, description)

        repo_name = request.form.get("repo_name", "").strip()
        repo_path = request.form.get("repo_path", "").strip()

        if repo_path:
            if not repo_name:
                flash("Repository name is required when providing a path.", "error")
                return render_template("projects/new.html"), 400

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

    return render_template("projects/new.html")


@bp.get("/<int:project_id>")
def view_project(project_id: int):
    db = get_db()
    project = models.get_project(db, project_id)
    if project is None:
        return render_template("404.html"), 404
    return render_template("projects/detail.html", project=project)


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
