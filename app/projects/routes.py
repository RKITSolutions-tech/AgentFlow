from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.db import get_db
from app.projects import models
from app.security import PathNotAllowedError

bp = Blueprint("projects", __name__, url_prefix="/projects")


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
