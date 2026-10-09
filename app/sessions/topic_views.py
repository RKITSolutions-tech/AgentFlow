"""Topics pages (docs/SESSION_TOPICS.md §5.3): a project's Topics list and one
Topic's page (full rolling summary, every session in it, start-in-Topic).

Row actions (rename/archive/restore) are AJAX per CLAUDE.md, returning JSON to
`X-Requested-With: XMLHttpRequest` callers and flash + redirect otherwise.
"""
from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from app.agents.models import is_archived, session_title
from app.db import get_db
from app.projects import models as project_models
from app.sessions import topics as topic_models
from app.sessions.topics import TopicError

bp = Blueprint("topics", __name__, url_prefix="/projects/<int:project_id>/topics")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _project_or_404(project_id: int):
    project = project_models.get_project(get_db(), project_id)
    if project is None:
        return None, (render_template("404.html"), 404)
    return project, None


def _respond(payload: dict, redirect_to: str, status: int = 200):
    if _wants_json():
        return jsonify(payload), status
    flash(payload.get("message") or payload.get("error", ""), "error" if "error" in payload else "info")
    return redirect(redirect_to)


@bp.get("/")
def list_topics(project_id: int):
    project, error = _project_or_404(project_id)
    if error:
        return error
    show_archived = request.args.get("archived") == "1"
    topics = topic_models.list_topics(get_db(), project_id, include_archived=show_archived)
    if show_archived:
        topics = [t for t in topics if t.archived]
    return render_template(
        "sessions/topics.html", project=project, topics=topics, show_archived=show_archived,
    )


@bp.post("/")
def create_topic(project_id: int):
    project, error = _project_or_404(project_id)
    if error:
        return error
    back = url_for("topics.list_topics", project_id=project_id)
    try:
        topic_id = topic_models.create_topic(get_db(), project_id, request.form.get("name", ""))
    except TopicError as exc:
        return _respond({"error": str(exc)}, back, 400)
    url = url_for("topics.view_topic", project_id=project_id, topic_id=topic_id)
    return _respond({"status": "created", "topic_id": topic_id, "redirect": url, "message": "Topic created."}, url)


@bp.get("/<int:topic_id>")
def view_topic(project_id: int, topic_id: int):
    project, error = _project_or_404(project_id)
    if error:
        return error
    db = get_db()
    try:
        topic = topic_models.get_project_topic(db, project_id, topic_id)
    except TopicError:
        return render_template("404.html"), 404
    sessions = topic_models.topic_sessions(db, topic_id)
    return render_template(
        "sessions/topic.html", project=project, topic=topic, sessions=sessions,
        titles={s.id: session_title(db, s) for s in sessions},
        archived={s.id: is_archived(s) for s in sessions},
    )


def _topic_or_error(project_id: int, topic_id: int):
    try:
        return topic_models.get_project_topic(get_db(), project_id, topic_id), None
    except TopicError as exc:
        return None, _respond({"error": str(exc)}, url_for("topics.list_topics", project_id=project_id), 404)


def _rename(project_id: int, topic_id: int, name: str):
    back = url_for("topics.view_topic", project_id=project_id, topic_id=topic_id)
    try:
        name = topic_models.rename_topic(get_db(), topic_id, name)
    except TopicError as exc:
        return _respond({"error": str(exc)}, back, 400)
    return _respond({"status": "renamed", "name": name, "message": "Topic renamed."}, back)


def _set_status(project_id: int, topic_id: int, status: str):
    try:
        topic_models.set_topic_status(get_db(), topic_id, status)
    except TopicError as exc:
        return _respond({"error": str(exc)}, url_for("topics.list_topics", project_id=project_id), 400)
    message = "Topic archived." if status == "archived" else "Topic restored."
    return _respond(
        {"status": status, "message": message},
        url_for("topics.list_topics", project_id=project_id, archived="1" if status == "active" else None),
    )


@bp.post("/<int:topic_id>/rename")
def rename_topic(project_id: int, topic_id: int):
    topic, error = _topic_or_error(project_id, topic_id)
    if error:
        return error
    return _rename(project_id, topic_id, request.form.get("name") or request.form.get("new_name", ""))


@bp.post("/<int:topic_id>/archive")
def archive_topic(project_id: int, topic_id: int):
    topic, error = _topic_or_error(project_id, topic_id)
    if error:
        return error
    return _set_status(project_id, topic_id, "archived")


@bp.post("/<int:topic_id>/restore")
def restore_topic(project_id: int, topic_id: int):
    topic, error = _topic_or_error(project_id, topic_id)
    if error:
        return error
    return _set_status(project_id, topic_id, "active")


@bp.patch("/<int:topic_id>")
def update_topic(project_id: int, topic_id: int):
    """JSON API form of rename/archive: {"name": ...} and/or {"status": ...}."""
    topic, error = _topic_or_error(project_id, topic_id)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    db = get_db()
    try:
        if "name" in body:
            topic_models.rename_topic(db, topic_id, str(body["name"]))
        if "status" in body:
            topic_models.set_topic_status(db, topic_id, str(body["status"]))
    except TopicError as exc:
        return jsonify({"error": str(exc)}), 400
    topic = topic_models.get_topic(db, topic_id)
    return jsonify({"status": "updated", "name": topic.name, "topic_status": topic.status})
