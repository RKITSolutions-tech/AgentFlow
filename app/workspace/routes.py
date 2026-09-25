from __future__ import annotations

import json
import os

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    stream_with_context,
    url_for,
)

from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.projects import models as project_models
from app.security import PathNotAllowedError
from app.workspace import files, git, search, terminal, terminal_models

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
    except (PermissionError, OSError):
        flash(f"Permission denied or cannot access {path}", "error")
        abort(404)

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
    except PathNotAllowedError:
        abort(404)
    except files.FileNotFoundInRepositoryError:
        flash(f"File not found: {path}", "error")
        abort(404)
    except (PermissionError, OSError):
        flash(f"Cannot access file: {path}", "error")
        abort(404)

    return render_template(
        "workspace/view.html",
        project=project,
        repo=repo,
        file=content,
        current_path=path,
        breadcrumbs=_breadcrumbs(path),
    )


@bp.get("/files/download")
def download_file(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    path = request.args.get("path", "")

    try:
        file_path = files.resolve_path(repo.path, path)
        if not os.path.isfile(file_path):
            abort(404)
    except PathNotAllowedError:
        abort(404)
    except (PermissionError, OSError):
        flash(f"Cannot access file: {path}", "error")
        abort(404)

    filename = os.path.basename(file_path)
    return send_file(file_path, as_attachment=True, download_name=filename)


def _file_action(project_id: int, repo_id: int, directory: str, operation, message: str):
    """Run a file operation; JSON for AJAX callers, flash + redirect otherwise.

    ``operation`` receives the repository root and returns a repo-relative
    path (or ``None``); ``directory`` is where the redirect lands.
    """
    _, repo = _get_project_and_repo(project_id, repo_id)
    try:
        result = operation(repo.path)
    except PathNotAllowedError:
        error, code = "That path is outside the repository.", 400
    except files.FileNotFoundInRepositoryError as exc:
        error, code = str(exc), 404
    except files.FileExistsInRepositoryError as exc:
        error, code = str(exc), 409
    except files.UploadTooLargeError as exc:
        error, code = str(exc), 413
    except files.FileOperationError as exc:
        error, code = str(exc), 400
    except OSError as exc:
        error, code = f"Filesystem error: {exc.strerror or exc}", 400
    else:
        if _wants_json():
            return {"status": "success", "message": message, "path": result}, 200
        flash(message, "success")
        return redirect(
            url_for("workspace.browse", project_id=project_id, repo_id=repo_id, path=directory)
        )
    if _wants_json():
        return {"error": error}, code
    flash(error, "error")
    return redirect(
        url_for("workspace.browse", project_id=project_id, repo_id=repo_id, path=directory)
    )


@bp.post("/files/create")
def create_entry(project_id: int, repo_id: int):
    directory = request.form.get("path", "")
    name = request.form.get("name", "")
    is_folder = request.form.get("kind") == "folder"
    operation = files.create_folder if is_folder else files.create_file
    return _file_action(
        project_id,
        repo_id,
        directory,
        lambda root: operation(root, directory, name),
        f"Created {'folder' if is_folder else 'file'} {name.strip()}",
    )


@bp.post("/files/rename")
def rename_entry(project_id: int, repo_id: int):
    path = request.values.get("path", "")
    new_name = request.form.get("new_name", "")
    return _file_action(
        project_id,
        repo_id,
        os.path.dirname(path.strip("/")),
        lambda root: files.rename_entry(root, path, new_name),
        f"Renamed to {new_name.strip()}",
    )


@bp.post("/files/delete")
def delete_entry(project_id: int, repo_id: int):
    # The path travels in the query string so the generic `data-ajax-action`
    # handler in app.js (which posts no body) can drive it.
    path = request.values.get("path", "")
    return _file_action(
        project_id,
        repo_id,
        os.path.dirname(path.strip("/")),
        lambda root: files.delete_entry(root, path),
        f"Deleted {os.path.basename(path.strip('/'))}",
    )


@bp.post("/files/upload")
def upload_files(project_id: int, repo_id: int):
    directory = request.form.get("path", "")
    uploads = [u for u in request.files.getlist("files") if u.filename]
    max_bytes = current_app.config.get("MAX_UPLOAD_BYTES", files.MAX_UPLOAD_SIZE)

    def operation(root: str):
        if not uploads:
            raise files.FileOperationError("Choose at least one file to upload.")
        saved = [
            files.save_upload(root, directory, u.filename, u.stream, max_bytes) for u in uploads
        ]
        return saved[0] if len(saved) == 1 else saved

    return _file_action(
        project_id,
        repo_id,
        directory,
        operation,
        f"Uploaded {len(uploads)} file{'s' if len(uploads) != 1 else ''}",
    )


@bp.get("/files/search")
def search_files(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    query = request.args.get("q", "")
    file_type = request.args.get("type", "")
    path_pattern = request.args.get("path", "")

    results = None
    error = None
    if query.strip():
        provider = HostExecutionProvider(
            current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
        )
        try:
            results = search.search(
                provider,
                repo.path,
                query,
                file_type=file_type,
                path_pattern=path_pattern,
                allowed_roots=current_app.config["ALLOWED_PROJECT_ROOTS"],
            )
        except (search.InvalidSearchQueryError, search.SearchExecutionError) as exc:
            error = str(exc)

    return render_template(
        "workspace/search.html",
        project=project,
        repo=repo,
        query=query,
        file_type=file_type,
        path_pattern=path_pattern,
        results=results,
        error=error,
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


@bp.get("/git/status")
def git_status(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    provider = HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )
    try:
        status = git.status(
            provider,
            repo.path,
            allowed_roots=current_app.config["ALLOWED_PROJECT_ROOTS"],
        )
    except git.GitCommandError as exc:
        flash(f"Git error: {exc}", "error")
        status = None

    return render_template(
        "workspace/git_status.html",
        project=project,
        repo=repo,
        status=status,
    )


@bp.get("/git/diff")
def git_diff(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    path = request.args.get("path", "")
    staged = request.args.get("staged", "false").lower() == "true"

    provider = HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )
    try:
        diff_data = git.diff(
            provider,
            repo.path,
            path=path,
            staged=staged,
            allowed_roots=current_app.config["ALLOWED_PROJECT_ROOTS"],
        )
    except git.GitCommandError as exc:
        flash(f"Git error: {exc}", "error")
        diff_data = None

    return render_template(
        "workspace/git_diff.html",
        project=project,
        repo=repo,
        diff=diff_data,
        path=path,
        staged=staged,
    )


@bp.get("/git/log")
def git_log(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    max_count = request.args.get("max_count", 20, type=int)
    max_count = max(1, min(max_count, 100))

    provider = HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )
    try:
        commits = git.log(
            provider,
            repo.path,
            max_count=max_count,
            allowed_roots=current_app.config["ALLOWED_PROJECT_ROOTS"],
        )
    except git.GitCommandError as exc:
        flash(f"Git error: {exc}", "error")
        commits = []

    return render_template(
        "workspace/git_log.html",
        project=project,
        repo=repo,
        commits=commits,
        max_count=max_count,
    )


@bp.post("/git/stage")
def git_stage_file(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    path = request.form.get("path", "").strip()

    if not path:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"error": "Path required"}, 400
        flash("Path required", "error")
        return redirect(url_for("workspace.git_status", project_id=project_id, repo_id=repo_id))

    provider = HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )
    try:
        git.stage(
            provider,
            repo.path,
            path,
            allowed_roots=current_app.config["ALLOWED_PROJECT_ROOTS"],
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"status": "success", "message": f"Staged {path}"}, 200
        flash(f"Staged {path}", "success")
    except (PathNotAllowedError, git.GitCommandError) as exc:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"error": str(exc)}, 400
        flash(f"Error: {exc}", "error")

    return redirect(url_for("workspace.git_status", project_id=project_id, repo_id=repo_id))


def _get_terminal_session(project_id: int, repo_id: int, term_id: int):
    _get_project_and_repo(project_id, repo_id)
    db = get_db()
    session = terminal_models.get_terminal_session(db, term_id)
    if session is None or session.repo_id != repo_id:
        abort(404)
    return db, session


@bp.get("/terminal")
def terminal_page(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    sessions = terminal_models.list_terminal_sessions(get_db(), repo_id)
    return render_template(
        "workspace/terminal.html",
        project=project,
        repo=repo,
        sessions=sessions,
    )


@bp.post("/terminal")
def create_terminal(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    db = get_db()
    label = request.values.get("label", "").strip()

    provider = HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )
    try:
        session = terminal.create_session(
            provider,
            db,
            current_app.config["DATABASE_PATH"],
            repo_id,
            repo.path,
            current_app.config["ALLOWED_PROJECT_ROOTS"],
            label=label,
        )
    except (PathNotAllowedError, terminal.TerminalError) as exc:
        return {"error": str(exc)}, 400

    return {
        "status": "success",
        "id": session.id,
        "label": session.label,
    }, 201


@bp.get("/terminal/<int:term_id>/stream")
def terminal_stream(project_id: int, repo_id: int, term_id: int):
    db, session = _get_terminal_session(project_id, repo_id, term_id)
    after_id = request.args.get("after_id", type=int, default=0)

    if session.status == "RUNNING":
        provider = HostExecutionProvider(
            current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
        )
        terminal.ensure_running(provider, db, current_app.config["DATABASE_PATH"], session)
        session = terminal_models.get_terminal_session(db, term_id)

    events = terminal_models.list_terminal_events(db, term_id, after_id=after_id)

    def generate():
        for event in events:
            payload = json.dumps({"id": event.id, "data": event.data})
            yield f"data: {payload}\n\n"
        if session.status != "RUNNING":
            payload = json.dumps({"id": None, "status": session.status})
            yield f"data: {payload}\n\n"

    return stream_with_context(generate()), 200, {"Content-Type": "text/event-stream"}


@bp.post("/terminal/<int:term_id>/input")
def terminal_input(project_id: int, repo_id: int, term_id: int):
    db, session = _get_terminal_session(project_id, repo_id, term_id)
    payload = request.get_json(silent=True) or {}
    text = payload.get("text")
    key = payload.get("key")
    if text is None and key is None:
        return {"error": "text or key required"}, 400

    provider = HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )
    try:
        terminal.send_input(provider, db, session, text=text, key=key)
    except terminal.TerminalError as exc:
        return {"error": str(exc)}, 400

    return {"status": "success"}, 200


@bp.post("/terminal/<int:term_id>/kill")
def terminal_kill(project_id: int, repo_id: int, term_id: int):
    db, session = _get_terminal_session(project_id, repo_id, term_id)
    provider = HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )
    try:
        terminal.kill_session(provider, db, session)
    except terminal.TerminalError as exc:
        return {"error": str(exc)}, 400

    return {"status": "success"}, 200


@bp.post("/git/unstage")
def git_unstage_file(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    path = request.form.get("path", "").strip()

    if not path:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"error": "Path required"}, 400
        flash("Path required", "error")
        return redirect(url_for("workspace.git_status", project_id=project_id, repo_id=repo_id))

    provider = HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )
    try:
        git.unstage(
            provider,
            repo.path,
            path,
            allowed_roots=current_app.config["ALLOWED_PROJECT_ROOTS"],
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"status": "success", "message": f"Unstaged {path}"}, 200
        flash(f"Unstaged {path}", "success")
    except (PathNotAllowedError, git.GitCommandError) as exc:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"error": str(exc)}, 400
        flash(f"Error: {exc}", "error")

    return redirect(url_for("workspace.git_status", project_id=project_id, repo_id=repo_id))


@bp.get("/git/commit")
def git_commit_page(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    provider = HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )

    try:
        status = git.status(
            provider,
            repo.path,
            allowed_roots=current_app.config["ALLOWED_PROJECT_ROOTS"],
        )
        # Get recent commits to show in the form
        commits = git.log(
            provider,
            repo.path,
            max_count=1,
            allowed_roots=current_app.config["ALLOWED_PROJECT_ROOTS"],
        )
        last_commit = commits[0] if commits else None
    except git.GitCommandError as exc:
        flash(f"Git error: {exc}", "error")
        status = None
        last_commit = None

    return render_template(
        "workspace/git_commit.html",
        project=project,
        repo=repo,
        status=status,
        last_commit=last_commit,
    )


@bp.post("/git/commit")
def git_create_commit(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    message = request.form.get("message", "").strip()
    amend = request.form.get("amend", "false").lower() == "true"

    if not message:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"error": "Commit message is required"}, 400
        flash("Commit message is required", "error")
        return redirect(
            url_for("workspace.git_commit_page", project_id=project_id, repo_id=repo_id)
        )

    provider = HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )

    try:
        commit_info = git.commit(
            provider,
            repo.path,
            message,
            amend=amend,
            allowed_roots=current_app.config["ALLOWED_PROJECT_ROOTS"],
        )

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {
                "status": "success",
                "message": f"Created commit {commit_info.short_hash}",
                "commit_hash": commit_info.hash,
            }, 200

        flash(f"Created commit {commit_info.short_hash}", "success")
        return redirect(
            url_for("workspace.git_log", project_id=project_id, repo_id=repo_id)
        )

    except (ValueError, git.GitCommandError) as exc:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"error": str(exc)}, 400
        flash(f"Error: {exc}", "error")
        return redirect(
            url_for("workspace.git_commit_page", project_id=project_id, repo_id=repo_id)
        )


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _git_provider() -> HostExecutionProvider:
    return HostExecutionProvider(
        current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
    )


@bp.get("/git/branches")
def git_branches(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    roots = current_app.config["ALLOWED_PROJECT_ROOTS"]
    try:
        branches = git.list_branches(_git_provider(), repo.path, allowed_roots=roots)
        remote = git.remote_status(_git_provider(), repo.path, allowed_roots=roots)
    except git.GitCommandError as exc:
        flash(f"Git error: {exc}", "error")
        branches, remote = [], None

    return render_template(
        "workspace/git_branches.html",
        project=project,
        repo=repo,
        branches=branches,
        remote=remote,
    )


def _branch_action(project_id: int, repo_id: int, action, success_message: str):
    """Run a branch operation, answering JSON for AJAX calls and redirecting otherwise."""
    return _branch_action_to(
        project_id, repo_id, action, success_message, "workspace.git_branches"
    )


def _branch_action_to(
    project_id: int, repo_id: int, action, success_message: str, endpoint: str
):
    _, repo = _get_project_and_repo(project_id, repo_id)
    try:
        action(
            _git_provider(),
            repo.path,
            allowed_roots=current_app.config["ALLOWED_PROJECT_ROOTS"],
        )
    except (ValueError, git.GitCommandError) as exc:  # PathNotAllowedError is a ValueError
        if _wants_json():
            return {"error": str(exc)}, 400
        flash(f"Error: {exc}", "error")
    else:
        if _wants_json():
            return {"status": "success", "message": success_message}, 200
        flash(success_message, "success")
    return redirect(url_for(endpoint, project_id=project_id, repo_id=repo_id))


@bp.post("/git/branches")
def git_create_branch(project_id: int, repo_id: int):
    name = request.form.get("name", "").strip()
    checkout = request.form.get("checkout") == "1"
    return _branch_action(
        project_id,
        repo_id,
        lambda provider, root, allowed_roots: git.create_branch(
            provider, root, name, checkout=checkout, allowed_roots=allowed_roots
        ),
        f"Created branch {name}",
    )


@bp.post("/git/branches/<path:name>/checkout")
def git_checkout_branch(project_id: int, repo_id: int, name: str):
    return _branch_action(
        project_id,
        repo_id,
        lambda provider, root, allowed_roots: git.checkout_branch(
            provider, root, name, allowed_roots=allowed_roots
        ),
        f"Switched to {name}",
    )


@bp.post("/git/branches/<path:name>/delete")
def git_delete_branch(project_id: int, repo_id: int, name: str):
    force = request.args.get("force") == "1"
    return _branch_action(
        project_id,
        repo_id,
        lambda provider, root, allowed_roots: git.delete_branch(
            provider, root, name, force=force, allowed_roots=allowed_roots
        ),
        f"Deleted branch {name}",
    )


@bp.post("/git/fetch")
def git_fetch(project_id: int, repo_id: int):
    return _branch_action(
        project_id,
        repo_id,
        lambda provider, root, allowed_roots: git.fetch(provider, root, allowed_roots=allowed_roots),
        "Fetched from origin",
    )


@bp.post("/git/pull")
def git_pull(project_id: int, repo_id: int):
    return _branch_action(
        project_id,
        repo_id,
        lambda provider, root, allowed_roots: git.pull(provider, root, allowed_roots=allowed_roots),
        "Pulled (fast-forward only)",
    )


@bp.post("/git/push")
def git_push(project_id: int, repo_id: int):
    return _branch_action(
        project_id,
        repo_id,
        lambda provider, root, allowed_roots: git.push(provider, root, allowed_roots=allowed_roots),
        "Pushed",
    )


@bp.post("/git/publish")
def git_publish(project_id: int, repo_id: int):
    return _branch_action(
        project_id,
        repo_id,
        lambda provider, root, allowed_roots: git.publish_branch(
            provider, root, allowed_roots=allowed_roots
        ),
        "Published branch to origin",
    )


def _path_action(project_id: int, repo_id: int, operation, verb: str):
    path = request.form.get("path", "").strip()
    return _branch_action_to(
        project_id,
        repo_id,
        lambda provider, root, allowed_roots: operation(
            provider, root, path, allowed_roots=allowed_roots
        ),
        f"{verb} {path}",
        "workspace.git_status",
    )


@bp.post("/git/discard")
def git_discard(project_id: int, repo_id: int):
    return _path_action(project_id, repo_id, git.discard_changes, "Discarded changes to")


@bp.post("/git/delete-untracked")
def git_delete_untracked(project_id: int, repo_id: int):
    return _path_action(project_id, repo_id, git.delete_untracked, "Deleted")


@bp.post("/git/undo-commit")
def git_undo_commit(project_id: int, repo_id: int):
    return _branch_action_to(
        project_id,
        repo_id,
        lambda provider, root, allowed_roots: git.undo_last_commit(
            provider, root, allowed_roots=allowed_roots
        ),
        "Undid the last commit; its changes are staged",
        "workspace.git_status",
    )


@bp.get("/git/worktrees")
def git_worktrees(project_id: int, repo_id: int):
    project, repo = _get_project_and_repo(project_id, repo_id)
    roots = current_app.config["ALLOWED_PROJECT_ROOTS"]
    db = get_db()
    try:
        worktrees = git.list_worktrees(_git_provider(), repo.path, allowed_roots=roots)
    except git.GitCommandError as exc:
        flash(f"Git error: {exc}", "error")
        worktrees = []
    current = os.path.realpath(repo.path)
    rows = [
        {
            "info": w,
            "current": os.path.realpath(w.path) == current,
            "repo": project_models.find_repository_by_path(db, project_id, os.path.realpath(w.path))
            or project_models.find_repository_by_path(db, project_id, w.path),
        }
        for w in worktrees
    ]
    return render_template(
        "workspace/git_worktrees.html", project=project, repo=repo, rows=rows
    )


def _worktree_reply(project_id: int, repo_id: int, operation, message: str):
    """Run ``operation(repo)``; JSON for AJAX, otherwise flash + back to the list."""
    _, repo = _get_project_and_repo(project_id, repo_id)
    try:
        operation(repo)
    except (ValueError, git.GitCommandError) as exc:  # PathNotAllowedError is a ValueError
        if _wants_json():
            return {"error": str(exc)}, 400
        flash(f"Error: {exc}", "error")
    else:
        if _wants_json():
            return {"status": "success", "message": message}, 200
        flash(message, "success")
    return redirect(url_for("workspace.git_worktrees", project_id=project_id, repo_id=repo_id))


@bp.post("/git/worktrees")
def git_create_worktree(project_id: int, repo_id: int):
    branch = request.form.get("branch", "").strip()
    base = request.form.get("base", "").strip() or None
    new_branch = request.form.get("existing") != "1"
    register = request.form.get("register", "1") == "1"
    roots = current_app.config["ALLOWED_PROJECT_ROOTS"]

    def operation(repo):
        path = git.add_worktree(
            _git_provider(), repo.path, branch, base=base, new_branch=new_branch,
            allowed_roots=roots,
        )
        if register:
            project_models.add_repository(
                get_db(), project_id, branch.replace("/", "-"), path, roots
            )

    return _worktree_reply(
        project_id, repo_id, operation, f"Created worktree for {branch}"
    )


@bp.post("/git/worktrees/remove")
def git_remove_worktree(project_id: int, repo_id: int):
    path = request.values.get("path", "")
    force = request.values.get("force") == "1"
    roots = current_app.config["ALLOWED_PROJECT_ROOTS"]

    def operation(repo):
        git.remove_worktree(_git_provider(), repo.path, path, force=force, allowed_roots=roots)
        db = get_db()
        linked = project_models.find_repository_by_path(db, project_id, os.path.realpath(path))
        if linked is not None and linked.id != repo.id:
            project_models.remove_repository(db, project_id, linked.id)

    return _worktree_reply(project_id, repo_id, operation, "Removed worktree")


@bp.post("/git/worktrees/merge")
def git_merge_worktree(project_id: int, repo_id: int):
    branch = request.values.get("branch", "").strip()
    roots = current_app.config["ALLOWED_PROJECT_ROOTS"]
    return _worktree_reply(
        project_id,
        repo_id,
        lambda repo: git.merge_branch(_git_provider(), repo.path, branch, allowed_roots=roots),
        f"Merged {branch}",
    )


@bp.post("/git/worktrees/register")
def git_register_worktree(project_id: int, repo_id: int):
    """Add an existing worktree of this repository as a project repository."""
    path = request.values.get("path", "")
    roots = current_app.config["ALLOWED_PROJECT_ROOTS"]

    def operation(repo):
        known = {
            os.path.realpath(w.path): w
            for w in git.list_worktrees(_git_provider(), repo.path, allowed_roots=roots)
        }
        worktree = known.get(os.path.realpath(path))
        if worktree is None:
            raise git.GitCommandError("That is not a worktree of this repository")
        name = (worktree.branch or os.path.basename(worktree.path)).replace("/", "-")
        project_models.add_repository(get_db(), project_id, name, worktree.path, roots)

    return _worktree_reply(project_id, repo_id, operation, "Added as a repository")
