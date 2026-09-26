from __future__ import annotations

import csv
import io

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from app.artifacts import collector, comparator, models
from app.db import get_db
from app.projects import models as project_models
from app.runs import artifacts as run_artifacts

bp = Blueprint("artifacts", __name__, url_prefix="/projects/<int:project_id>/artifacts")

PAGE_SIZE = 30
_INLINE_IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
PREVIEW_BYTES = 64 * 1024


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _root() -> str:
    return run_artifacts.artifact_root(current_app.config)


def _project(project_id: int):
    project = project_models.get_project(get_db(), project_id)
    if project is None:
        abort(404)
    return project


def _artifact(project_id: int, artifact_id: int) -> models.Artifact:
    artifact = models.get_artifact(get_db(), artifact_id)
    if artifact is None or artifact.project_id != project_id:
        abort(404)
    return artifact


def _reply(message: str, ok: bool, target: str, code: int = 400, **extra):
    if _wants_json():
        if ok:
            return {"status": "success", "message": message, **extra}, 200
        return {"error": message}, code
    flash(message, "success" if ok else "error")
    return redirect(target)


def _int(name: str) -> int | None:
    value = request.args.get(name, type=int)
    return value if value and value > 0 else None


def _filters() -> dict:
    kinds = [k for k in request.args.getlist("kind") if k in models.KINDS]
    return {
        "kinds": kinds or None,
        "since": request.args.get("since") or None,
        "until": request.args.get("until") or None,
        "step_name": request.args.get("step") or None,
        "execution_id": _int("execution"),
        "ralph_run_id": _int("ralph_run"),
        "iteration": _int("iteration"),
        "tag": request.args.get("tag") or None,
        "q": (request.args.get("q") or "").strip() or None,
    }


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@bp.get("")
def library(project_id: int):
    project = _project(project_id)
    db = get_db()
    page = max(request.args.get("page", 1, type=int), 1)
    filters = _filters()
    items, total = models.search(db, project_id, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, **filters)
    query = {k: v for k, v in request.args.to_dict(flat=False).items() if k != "page"}
    return render_template(
        "artifacts/list.html", project=project, artifacts=items, total=total, page=page,
        pages=max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1), filters=filters, kinds=models.KINDS,
        step_names=models.step_names(db, project_id), args=request.args, query=query,
        preview_ok=lambda a: a.name.lower().endswith(_INLINE_IMAGES) and a.kind == "screenshot",
    )


@bp.get("/search")
def search_json(project_id: int):
    _project(project_id)
    items, total = models.search(get_db(), project_id, limit=50, **_filters())
    return {
        "total": total,
        "results": [
            {"id": a.id, "name": a.name, "kind": a.kind, "step": a.step_name, "tags": a.tags,
             "url": url_for("artifacts.detail", project_id=project_id, artifact_id=a.id)}
            for a in items
        ],
    }


@bp.get("/export.csv")
def export_csv(project_id: int):
    _project(project_id)
    items, _ = models.search(get_db(), project_id, limit=10000, **_filters())
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "kind", "name", "size", "step", "execution", "ralph_run", "iteration", "tags", "created_at", "url"])
    for a in items:
        writer.writerow([_csv_safe(v) for v in (
            a.id, a.kind, a.name, a.size, a.step_name, a.execution_id, a.ralph_run_id, a.iteration_number,
            " ".join(a.tags), a.created_at,
            url_for("artifacts.detail", project_id=project_id, artifact_id=a.id, _external=True),
        )])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=artifacts.csv"})


@bp.get("/<int:artifact_id>")
def detail(project_id: int, artifact_id: int):
    project = _project(project_id)
    artifact = _artifact(project_id, artifact_id)
    text = None
    if artifact.kind in collector.TEXT_KINDS:
        try:
            with open(collector.file_path(_root(), artifact), "rb") as fh:
                text = fh.read(PREVIEW_BYTES).decode("utf-8", "replace")
        except (OSError, run_artifacts.ArtifactPathError):
            text = "(file missing)"
    return render_template(
        "artifacts/detail.html", project=project, artifact=artifact, text=text,
        inline_image=artifact.kind == "screenshot" and artifact.name.lower().endswith(_INLINE_IMAGES),
    )


@bp.get("/<int:artifact_id>/preview")
def preview(project_id: int, artifact_id: int):
    """Inline preview for raster images and text only. SVG/HTML are never
    rendered inline: they can carry script, so they only download."""
    _project(project_id)
    artifact = _artifact(project_id, artifact_id)
    try:
        path = collector.file_path(_root(), artifact)
    except run_artifacts.ArtifactPathError:
        abort(404)
    if artifact.kind == "screenshot" and artifact.name.lower().endswith(_INLINE_IMAGES):
        response = send_file(path, mimetype=artifact.mime_type, as_attachment=False)
    elif artifact.kind in collector.TEXT_KINDS:
        with open(path, "rb") as fh:
            response = Response(fh.read(PREVIEW_BYTES), mimetype="text/plain")
    else:
        abort(415)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    return response


@bp.get("/<int:artifact_id>/download")
def download(project_id: int, artifact_id: int):
    _project(project_id)
    artifact = _artifact(project_id, artifact_id)
    try:
        path = collector.file_path(_root(), artifact)
    except run_artifacts.ArtifactPathError:
        abort(404)
    return send_file(path, as_attachment=True, download_name=artifact.name)


@bp.post("/<int:artifact_id>/tags")
def edit_tags(project_id: int, artifact_id: int):
    _project(project_id)
    _artifact(project_id, artifact_id)
    back = url_for("artifacts.detail", project_id=project_id, artifact_id=artifact_id)
    db = get_db()
    remove = request.form.get("remove", "").strip()
    if remove:
        models.remove_tag(db, artifact_id, remove)
    tags = [t for t in request.form.get("tags", "").replace(",", " ").split() if t]
    if tags:
        models.add_tags(db, artifact_id, tags)
    if not remove and not tags:
        return _reply("Enter at least one tag", False, back)
    return _reply("Tags updated", True, back)


@bp.post("/compare")
def compare(project_id: int):
    _project(project_id)
    back = url_for("artifacts.library", project_id=project_id)
    try:
        a = _artifact(project_id, int(request.values.get("a", "")))
        b = _artifact(project_id, int(request.values.get("b", "")))
    except ValueError:
        return _reply("Choose two artifacts to compare", False, back)
    try:
        kind, result = comparator.compare(_root(), a, b)
    except (comparator.ComparisonError, OSError, run_artifacts.ArtifactPathError) as exc:
        return _reply(str(exc), False, back, 422)
    comparison_id = models.save_comparison(get_db(), a.id, b.id, kind, result)
    target = url_for("artifacts.view_comparison", project_id=project_id, comparison_id=comparison_id)
    return _reply("Compared", True, target, comparison_id=comparison_id, redirect=target)


@bp.get("/compare/<int:comparison_id>")
def view_comparison(project_id: int, comparison_id: int):
    project = _project(project_id)
    comparison = models.get_comparison(get_db(), comparison_id)
    if comparison is None:
        abort(404)
    a = _artifact(project_id, comparison.artifact_a_id)
    b = _artifact(project_id, comparison.artifact_b_id)
    return render_template("artifacts/compare.html", project=project, comparison=comparison, a=a, b=b)
