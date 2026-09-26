"""Prompt library UI (fragments, templates, Ralph instruction blocks)."""
from __future__ import annotations

import json

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for

from app.db import get_db
from app.projects import models as project_models
from app.prompts import assembler, models
from app.prompts.models import LibraryError

bp = Blueprint("prompts", __name__, url_prefix="/prompts")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _reply(message: str, ok: bool, target: str, code: int = 400, **extra):
    if _wants_json():
        if ok:
            return {"status": "success", "message": message, **extra}, 200
        return {"error": message}, code
    flash(message, "success" if ok else "error")
    return redirect(target)


def _library() -> str:
    return url_for("prompts.library")


def _form_ints(name: str) -> list[int]:
    return [int(v) for v in request.form.getlist(name) if v.strip().isdigit()]


@bp.get("/library")
def library():
    db = get_db()
    return render_template(
        "prompts/library.html", fragments=models.list_fragments(db), templates=models.list_templates(db),
        categories=models.CATEGORIES, usage=models.template_usage(db),
    )


@bp.post("/library")
def create_fragment():
    try:
        fid = models.create_fragment(
            get_db(), request.form.get("name", ""), request.form.get("content", ""),
            request.form.get("category", "instruction"), request.form.get("tags", ""),
        )
    except LibraryError as exc:
        return _reply(str(exc), False, _library())
    return _reply("Fragment created", True, _library(), fragment_id=fid, redirect=_library())


@bp.route("/fragments/<int:fragment_id>", methods=["GET", "POST"])
def edit_fragment(fragment_id: int):
    db = get_db()
    fragment = models.get_fragment(db, fragment_id)
    if fragment is None:
        abort(404)
    if request.method == "GET":
        return render_template(
            "prompts/fragment_form.html", fragment=fragment, categories=models.CATEGORIES,
            versions=models.fragment_versions(db, fragment_id),
            used_by=models.templates_using_fragment(db, fragment_id),
        )
    try:
        models.update_fragment(
            db, fragment_id, request.form.get("name"), request.form.get("content"),
            request.form.get("category"), request.form.get("tags"),
        )
    except LibraryError as exc:
        return _reply(str(exc), False, request.url)
    return _reply("Fragment saved", True, request.url)


@bp.route("/fragments/<int:fragment_id>/delete", methods=["POST", "DELETE"])
def delete_fragment(fragment_id: int):
    if models.get_fragment(get_db(), fragment_id) is None:
        abort(404)
    try:
        models.delete_fragment(get_db(), fragment_id)
    except LibraryError as exc:
        return _reply(str(exc), False, _library(), 409)
    return _reply("Fragment deleted", True, _library())


def _template_fields() -> dict:
    variables = {}
    for line in request.form.get("variables", "").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip():
            variables[key.strip()] = value.strip()
    base = request.form.get("base_template_id", "")
    return {
        "body": request.form.get("body", ""), "fragments": _form_ints("fragments"),
        "description": request.form.get("description", ""), "variables": variables,
        "base_template_id": int(base) if base.isdigit() else None,
    }


@bp.route("/templates", methods=["GET", "POST"])
def templates():
    db = get_db()
    if request.method == "GET":
        return render_template(
            "prompts/template_form.html", template=None, fragments=models.list_fragments(db),
            templates=models.list_templates(db),
        )
    try:
        tid = models.create_template(db, request.form.get("name", ""), **_template_fields())
    except LibraryError as exc:
        return _reply(str(exc), False, url_for("prompts.templates"))
    target = url_for("prompts.edit_template", template_id=tid)
    return _reply("Template created", True, target, template_id=tid, redirect=target)


@bp.route("/templates/<int:template_id>", methods=["GET", "POST"])
def edit_template(template_id: int):
    db = get_db()
    template = models.get_template(db, template_id)
    if template is None:
        abort(404)
    if request.method == "GET":
        return render_template(
            "prompts/template_form.html", template=template, fragments=models.list_fragments(db),
            templates=[t for t in models.list_templates(db) if t.id != template_id],
        )
    try:
        models.update_template(db, template_id, name=request.form.get("name"), **_template_fields())
    except LibraryError as exc:
        return _reply(str(exc), False, request.url)
    return _reply("Template saved", True, request.url)


@bp.route("/templates/<int:template_id>/delete", methods=["POST", "DELETE"])
def delete_template(template_id: int):
    if models.get_template(get_db(), template_id) is None:
        abort(404)
    try:
        models.delete_template(get_db(), template_id)
    except LibraryError as exc:
        return _reply(str(exc), False, _library(), 409)
    return _reply("Template deleted", True, _library())


@bp.post("/preview")
def preview():
    """Assemble without saving. Either a saved template (`template_id`) or the
    unsaved form fields (`body`, `fragments`, `base_template_id`) can be previewed;
    `repository_id`/`project_id` enable @file and context-glob resolution."""
    db = get_db()
    variables = {}
    for line in request.form.get("preview_variables", "").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip():
            variables[key.strip()] = value
    root = None
    project_id, repo_id = request.form.get("project_id", ""), request.form.get("repository_id", "")
    if project_id.isdigit() and repo_id.isdigit():
        repo = project_models.get_repository(db, int(project_id), int(repo_id))
        root = repo.path if repo else None
    try:
        if request.form.get("template_id", "").isdigit():
            template = models.get_template(db, int(request.form["template_id"]))
            if template is None:
                abort(404)
        else:
            fields = _template_fields()
            template = models.PromptTemplate(
                0, "(preview)", fields["description"], fields["body"].strip(), fields["fragments"],
                fields["variables"], fields["base_template_id"], "", "",
            )
            for fid in template.fragments:
                if models.get_fragment(db, fid) is None:
                    raise LibraryError(f"Fragment {fid} does not exist")
        blocks_text, blocks = "", []
        if request.form.get("include_ralph") == "on":
            blocks_text, blocks = assembler.include_ralph_instructions(
                models.effective_blocks(db, int(project_id) if project_id.isdigit() else None),
                request.form.get("agent_type") or None,
            )
        result = assembler.assemble_effective_prompt(
            db, template=template, text=request.form.get("text", ""), variables=variables, root=root,
            allowed_roots=tuple(current_app.config["ALLOWED_PROJECT_ROOTS"]),
            context_globs=[l for l in request.form.get("context_files", "").splitlines() if l.strip()],
            project_id=int(project_id) if project_id.isdigit() and project_models.get_project(db, int(project_id)) else None,
        )
    except LibraryError as exc:
        return {"error": str(exc)}, 400
    if blocks_text:
        result.text += "\n\n" + blocks_text
        result.blocks_included = blocks
    return {"prompt": result.text, "metadata": result.metadata()}


@bp.get("/ralph-blocks")
def ralph_blocks():
    db = get_db()
    return render_template("prompts/ralph_blocks.html", blocks=models.list_blocks(db), usage=models.block_usage(db))


@bp.post("/ralph-blocks")
def create_block():
    back = url_for("prompts.ralph_blocks")
    try:
        bid = models.create_block(
            get_db(), request.form.get("name", ""), request.form.get("content", ""),
            request.form.get("description", ""), applies_to=request.form.get("applies_to", ""),
        )
    except LibraryError as exc:
        return _reply(str(exc), False, back)
    return _reply("Block added", True, back, block_id=bid, redirect=back)


@bp.post("/ralph-blocks/<int:block_id>")
def update_block(block_id: int):
    """Toggle, edit or reorder: `enabled` ('1'/'0'), `content`, `name`,
    `description`, `applies_to`, and `move` ('up'/'down')."""
    db = get_db()
    if models.get_block(db, block_id) is None:
        abort(404)
    back = url_for("prompts.ralph_blocks")
    form = request.form
    try:
        if form.get("move"):
            models.move_block(db, block_id, form["move"])
        else:
            models.update_block(
                db, block_id, form.get("name"), form.get("content"), form.get("description"),
                (form["enabled"] == "1") if "enabled" in form else None, form.get("applies_to"),
            )
    except LibraryError as exc:
        return _reply(str(exc), False, back)
    return _reply("Saved", True, back)


@bp.route("/ralph-blocks/<int:block_id>/delete", methods=["POST", "DELETE"])
def delete_block(block_id: int):
    if models.get_block(get_db(), block_id) is None:
        abort(404)
    models.delete_block(get_db(), block_id)
    return _reply("Block deleted", True, url_for("prompts.ralph_blocks"))


# -- import / export ------------------------------------------------------------------------------


@bp.get("/export")
def export_library():
    body = json.dumps(models.export_library(get_db()), indent=2)
    return Response(body, mimetype="application/json", headers={"Content-Disposition": "attachment; filename=prompt-library.json"})


@bp.post("/import")
def import_library():
    """Merge an export (uploaded `file` or pasted `json`); `overwrite=on` replaces
    the content of names that already exist."""
    upload = request.files.get("file")
    raw = upload.read(2_000_000).decode("utf-8", "replace") if upload and upload.filename else request.form.get("json", "")
    try:
        data = json.loads(raw)
        result = models.import_library(get_db(), data, overwrite=request.form.get("overwrite") == "on")
    except (json.JSONDecodeError, LibraryError) as exc:
        return _reply(f"Import failed: {exc}", False, _library())
    message = f"Imported: {result['created']} created, {result['updated']} updated, {result['skipped']} skipped"
    if result["errors"]:
        message += f"; {len(result['errors'])} problem(s): " + "; ".join(result["errors"][:5])
    return _reply(message, not result["errors"] or bool(result["created"] or result["updated"]), _library(), result=result)


# -- per-project prompt settings (context files, overrides, recorded prompts) ----------------------------


def _project(project_id: int):
    project = project_models.get_project(get_db(), project_id)
    if project is None:
        abort(404)
    return project


def _project_page(project_id: int) -> str:
    return url_for("prompts.project_prompts", project_id=project_id)


@bp.get("/projects/<int:project_id>")
def project_prompts(project_id: int):
    project = _project(project_id)
    db = get_db()
    source = request.args.get("source", "")
    root, files, skipped = assembler.project_context(db, project_id, tuple(current_app.config["ALLOWED_PROJECT_ROOTS"]))
    return render_template(
        "prompts/project.html", project=project, templates=models.list_templates(db),
        blocks=models.list_blocks(db), template_overrides=models.get_overrides(db, project_id, "template"),
        block_overrides=models.get_overrides(db, project_id, "block"),
        context_files=files, context_skipped=skipped,
        discovered=not [l for l in project.context_files.splitlines() if l.strip()],
        recorded=models.list_recorded(db, project_id, source if source in ("pipeline_step", "ralph_iteration") else ""),
        source=source,
    )


@bp.post("/projects/<int:project_id>/context")
def set_context_files(project_id: int):
    _project(project_id)
    try:
        project_models.set_context_files(get_db(), project_id, request.form.get("context_files", ""))
    except ValueError as exc:
        return _reply(str(exc), False, _project_page(project_id))
    return _reply("Context files saved", True, _project_page(project_id))


@bp.post("/projects/<int:project_id>/overrides/<kind>/<int:target_id>")
def set_override(project_id: int, kind: str, target_id: int):
    _project(project_id)
    enabled = {"1": True, "0": False}.get(request.form.get("enabled", ""))
    try:
        models.set_override(get_db(), project_id, kind, target_id, request.form.get("content", ""), enabled)
    except LibraryError as exc:
        return _reply(str(exc), False, _project_page(project_id))
    return _reply("Override saved", True, _project_page(project_id))


@bp.route("/projects/<int:project_id>/overrides/<kind>/<int:target_id>/delete", methods=["POST", "DELETE"])
def clear_override(project_id: int, kind: str, target_id: int):
    _project(project_id)
    models.clear_override(get_db(), project_id, kind, target_id)
    return _reply("Override removed", True, _project_page(project_id))


@bp.get("/projects/<int:project_id>/recorded/<int:prompt_id>")
def recorded_prompt(project_id: int, prompt_id: int):
    project = _project(project_id)
    found = models.get_recorded_for_project(get_db(), project_id, prompt_id)
    if found is None:
        abort(404)
    return render_template("prompts/recorded.html", project=project, **found)
