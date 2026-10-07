"""Registry + proxy UI for remote AgentFlow instances (the "master control
plane" feature): register another instance's federation API here, then list
its projects/sessions and drive a session (view/send/stop) without opening
that instance's own UI. Deliberately narrower than the local sessions UI --
no fork/rename/archive/attachments/model-switch for a remote session in this
first pass (app/templates/sessions/chat.html hardcodes far more endpoints
than this feature needs; see the plan this shipped from for why a trimmed
template was chosen over parameterizing that one).
"""

from __future__ import annotations

import json

from flask import Blueprint, flash, jsonify, redirect, render_template, request, stream_with_context, url_for

from app.db import get_db
from app.instances import models as instance_models
from app.instances.client import RemoteInstanceError
from app.instances import client

bp = Blueprint("instances", __name__, url_prefix="/instances")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@bp.get("")
def index():
    db = get_db()
    instances = instance_models.list_instances(db)
    statuses = {}
    for instance in instances:
        try:
            client.ping(instance)
            instance_models.set_status(db, instance.id, "ONLINE")
            statuses[instance.id] = "ONLINE"
        except RemoteInstanceError:
            instance_models.set_status(db, instance.id, "UNREACHABLE")
            statuses[instance.id] = "UNREACHABLE"
    instances = instance_models.list_instances(db)
    return render_template("instances/index.html", instances=instances, statuses=statuses)


@bp.post("/add")
def add_instance():
    db = get_db()
    name = request.form.get("name", "").strip()
    base_url = request.form.get("base_url", "").strip()
    token = request.form.get("token", "").strip()
    if not name or not base_url or not token:
        flash("Name, base URL and token are all required.", "error")
    else:
        instance_models.add_instance(db, name, base_url, token)
        flash(f"Added {name}.", "info")
    return redirect(url_for("instances.index"))


@bp.post("/<int:instance_id>/delete")
def delete_instance(instance_id: int):
    db = get_db()
    instance = instance_models.get_instance(db, instance_id)
    if instance is None:
        if _wants_json():
            return jsonify({"error": "Instance not found"}), 404
        return redirect(url_for("instances.index"))

    instance_models.delete_instance(db, instance_id)
    if _wants_json():
        return jsonify({"status": "deleted", "message": f"Removed {instance.name}."}), 200
    flash(f"Removed {instance.name}.", "info")
    return redirect(url_for("instances.index"))


def _instance_or_404(instance_id: int):
    instance = instance_models.get_instance(get_db(), instance_id)
    if instance is None:
        return None, (render_template("404.html"), 404)
    return instance, None


@bp.get("/<int:instance_id>/projects")
def remote_projects(instance_id: int):
    instance, error = _instance_or_404(instance_id)
    if error:
        return error
    try:
        projects = client.list_projects(instance)
    except RemoteInstanceError as e:
        flash(str(e), "error")
        return redirect(url_for("instances.index"))
    return render_template("instances/projects.html", instance=instance, projects=projects)


@bp.get("/<int:instance_id>/projects/<int:project_id>/sessions")
def remote_sessions(instance_id: int, project_id: int):
    instance, error = _instance_or_404(instance_id)
    if error:
        return error
    try:
        projects = client.list_projects(instance)
        project = next((p for p in projects if p["id"] == project_id), None)
        if project is None:
            flash("Project not found on that instance.", "error")
            return redirect(url_for("instances.remote_projects", instance_id=instance_id))
        summary = client.list_sessions(instance, project_id)
    except RemoteInstanceError as e:
        flash(str(e), "error")
        return redirect(url_for("instances.remote_projects", instance_id=instance_id))
    return render_template(
        "instances/sessions.html",
        instance=instance,
        project=project,
        total=summary["total"],
        sessions=summary["sessions"],
    )


@bp.post("/<int:instance_id>/projects/<int:project_id>/sessions")
def create_remote_session(instance_id: int, project_id: int):
    instance, error = _instance_or_404(instance_id)
    if error:
        return error
    fields = {
        "agent_type": request.form.get("agent_type", "fake"),
        "model": request.form.get("model", ""),
        "mcp_tools": request.form.get("mcp_tools") == "1",
    }
    repo_id = request.form.get("repo_id", "").strip()
    if repo_id:
        fields["repo_id"] = int(repo_id)
    try:
        session_id = client.create_session(instance, project_id, **fields)
    except RemoteInstanceError as e:
        flash(str(e), "error")
        return redirect(url_for("instances.remote_sessions", instance_id=instance_id, project_id=project_id))
    return redirect(url_for("instances.remote_chat", instance_id=instance_id, session_id=session_id))


@bp.get("/<int:instance_id>/sessions/<int:session_id>")
def remote_chat(instance_id: int, session_id: int):
    instance, error = _instance_or_404(instance_id)
    if error:
        return error
    try:
        detail = client.get_session(instance, session_id)
    except RemoteInstanceError as e:
        flash(str(e), "error")
        return redirect(url_for("instances.index"))
    return render_template("instances/chat.html", instance=instance, session=detail)


@bp.get("/<int:instance_id>/sessions/<int:session_id>/stream")
def remote_stream(instance_id: int, session_id: int):
    instance, error = _instance_or_404(instance_id)
    if error:
        return error
    after_id = request.args.get("after_id", type=int, default=0)
    try:
        events = client.stream(instance, session_id, after_id=after_id)
    except RemoteInstanceError as e:
        return jsonify({"error": str(e)}), 502

    def generate():
        for payload in events:
            yield f"data: {json.dumps(payload)}\n\n"

    return stream_with_context(generate()), 200, {"Content-Type": "text/event-stream"}


@bp.post("/<int:instance_id>/sessions/<int:session_id>/send")
def remote_send(instance_id: int, session_id: int):
    instance, error = _instance_or_404(instance_id)
    if error:
        return error
    prompt = request.form.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400
    try:
        client.send_prompt(instance, session_id, prompt)
    except RemoteInstanceError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"status": "sent"})


@bp.post("/<int:instance_id>/sessions/<int:session_id>/stop")
def remote_stop(instance_id: int, session_id: int):
    instance, error = _instance_or_404(instance_id)
    if error:
        return error
    try:
        client.stop(instance, session_id)
    except RemoteInstanceError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"status": "stopped"})
