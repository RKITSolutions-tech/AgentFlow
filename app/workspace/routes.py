from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.db import get_db
from app.projects import models as project_models
from app.security import PathNotAllowedError
from app.workspace import files

bp = Blueprint(
    "workspace",
    __name__,
    url_prefix="/projects/<int:project_id>/repos/<int:repo_id>",
)


def _get_project_and_repo(project_id: int, repo_id: int):
    db = get_db()
    project = project_models.get_project(db, project_id)
    if project is None:
        abort(404)
    repo = project_models.get_repository(db, project_id, repo_id)
    if repo is None:
        abort(404)
    return project, repo


def _breadcrumbs(path: str) -> list[dict]:
    parts = [p for p in path.split("/") if p]
    crumbs = []
    accum: list[str] = []
    for part in parts:
        accum.append(part)
        crumbs.append({"name": part, "path": "/".join(accum)})
    return crumbs


@bp.get("/files")
def browse(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    path = request.args.get("path", "")

    try:
        entries = files.list_directory(repo.path, path)
    except PathNotAllowedError:
        abort(404)
    except files.FileNotFoundInRepositoryError:
        return redirect(
            url_for("workspace.view_file", project_id=project_id, repo_id=repo_id, path=path)
        )

    parent_path = "/".join(path.split("/")[:-1]) if path else None

    return render_template(
        "workspace/browse.html",
        project=project,
        repo=repo,
        entries=entries,
        current_path=path,
        parent_path=parent_path,
        breadcrumbs=_breadcrumbs(path),
    )


@bp.get("/files/view")
def view_file(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    path = request.args.get("path", "")

    try:
        content = files.read_file(repo.path, path)
    except (PathNotAllowedError, files.FileNotFoundInRepositoryError):
        abort(404)

    return render_template(
        "workspace/view.html",
        project=project,
        repo=repo,
        file=content,
        current_path=path,
        breadcrumbs=_breadcrumbs(path),
    )


@bp.route("/files/edit", methods=["GET", "POST"])
def edit_file(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    path = request.values.get("path", "")

    if request.method == "POST":
        content = request.form.get("content", "")
        try:
            files.write_file(repo.path, path, content)
        except (PathNotAllowedError, files.FileNotFoundInRepositoryError):
            abort(404)
        flash(f"Saved {path}", "success")
        return redirect(
            url_for("workspace.view_file", project_id=project_id, repo_id=repo_id, path=path)
        )

    try:
        content = files.read_file(repo.path, path)
    except (PathNotAllowedError, files.FileNotFoundInRepositoryError):
        abort(404)

    if content.is_binary or content.too_large:
        flash("Binary or oversized files cannot be edited in the browser.", "error")
        return redirect(
            url_for("workspace.view_file", project_id=project_id, repo_id=repo_id, path=path)
        )

    return render_template(
        "workspace/edit.html",
        project=project,
        repo=repo,
        file=content,
        current_path=path,
        breadcrumbs=_breadcrumbs(path),
    )
