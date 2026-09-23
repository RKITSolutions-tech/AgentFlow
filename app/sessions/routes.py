from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for, jsonify, stream_with_context

from app.db import get_db
from app.agents.models import create_agent_session, get_agent_session, list_agent_sessions_for_project, list_agent_events
from app.agents.codex import CodexAdapter
from app.agents.fake import FakeAgentAdapter
from app.projects import models as project_models

bp = Blueprint("sessions", __name__, url_prefix="/sessions")


@bp.get("")
def list_sessions():
    db = get_db()
    projects = project_models.list_projects(db)
    return render_template("sessions/list.html", projects=projects)


@bp.get("/project/<int:project_id>")
def project_sessions(project_id: int):
    db = get_db()
    project = project_models.get_project(db, project_id)
    if project is None:
        return render_template("404.html"), 404

    sessions = list_agent_sessions_for_project(db, project_id)
    return render_template(
        "sessions/project.html",
        project=project,
        sessions=sessions,
    )


@bp.post("/project/<int:project_id>/create")
def create_session(project_id: int):
    from app.agents.base import AgentContext
    from app.execution.host import HostExecutionProvider

    db = get_db()
    project = project_models.get_project(db, project_id)
    if project is None:
        return render_template("404.html"), 404

    if not project.repositories:
        flash("Project has no repositories. Add one before starting a session.", "error")
        return redirect(url_for("sessions.project_sessions", project_id=project_id))

    agent_type = request.form.get("agent_type", "codex").lower()

    # Get repo_id from form, handling both integer IDs and missing values
    repo_id_str = request.form.get("repo_id", "").strip()
    repo_id = None
    try:
        if repo_id_str and repo_id_str.isdigit():
            repo_id = int(repo_id_str)
    except (ValueError, TypeError):
        pass

    # Find the target repository
    if repo_id:
        repo = project_models.get_repository(db, project_id, repo_id)
        if repo is None:
            flash("Repository not found.", "error")
            return redirect(url_for("sessions.project_sessions", project_id=project_id))
        execution_target = repo.path
    else:
        # Use primary repository or first available
        primary_repo = next((r for r in project.repositories if r.is_primary), None)
        if primary_repo is None:
            primary_repo = project.repositories[0]
        execution_target = primary_repo.path
        repo_id = primary_repo.id

    try:
        # Initialize execution provider
        execution_provider = HostExecutionProvider(
            current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
        )

        # Create an execution context for the repository
        context_config = {
            "working_directory": execution_target,
            "environment": {},
            "target": "",
        }
        exec_context = execution_provider.create_context(context_config)

        if agent_type == "codex":
            adapter = CodexAdapter(db=db, execution_provider=execution_provider)
        else:
            adapter = FakeAgentAdapter(db=db)

        # Start the session with the execution context
        context = AgentContext(
            project_id=project_id,
            working_directory=execution_target,
            execution_provider="host",
            execution_target=str(exec_context.id),  # Pass context ID as string
        )

        session = adapter.start(context, "Starting session...")
        session_id = session.id

        flash(f"Session {session_id} started successfully.", "info")
    except Exception as e:
        flash(f"Error starting session: {e}", "error")
        return redirect(url_for("sessions.project_sessions", project_id=project_id))

    return redirect(url_for("sessions.view_session", session_id=session_id))


@bp.get("/<int:session_id>")
def view_session(session_id: int):
    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        return render_template("404.html"), 404

    project = project_models.get_project(db, session.project_id)
    repo_id = session.metadata.get("repo_id")
    repo = project_models.get_repository(db, session.project_id, repo_id) if repo_id else None

    return render_template(
        "sessions/chat.html",
        session=session,
        project=project,
        repo=repo,
    )


@bp.post("/<int:session_id>/send")
def send_prompt(session_id: int):
    from app.execution.host import HostExecutionProvider

    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    prompt = request.form.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400

    try:
        if session.agent_type.lower() == "codex":
            execution_provider = HostExecutionProvider(
                current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
            )
            adapter = CodexAdapter(db=db, execution_provider=execution_provider)
        else:
            adapter = FakeAgentAdapter(db=db)

        adapter.send(session_id, prompt)
        return jsonify({"status": "sent"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/<int:session_id>/stream")
def stream_output(session_id: int):
    import json

    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        return {"error": "Session not found"}, 404

    after_id = request.args.get("after_id", type=int, default=0)

    events = list_agent_events(db, session_id, after_id)

    def generate():
        for event in events:
            event_json = json.dumps({
                "id": event.id,
                "event_type": event.event_type,
                "data": event.data,
                "created_at": event.created_at,
            })
            yield f"data: {event_json}\n\n"

    return stream_with_context(generate()), 200, {"Content-Type": "text/event-stream"}


@bp.post("/<int:session_id>/stop")
def stop_session(session_id: int):
    from app.execution.host import HostExecutionProvider

    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        return redirect(url_for("sessions.list_sessions"))

    try:
        if session.agent_type.lower() == "codex":
            execution_provider = HostExecutionProvider(
                current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
            )
            adapter = CodexAdapter(db=db, execution_provider=execution_provider)
        else:
            adapter = FakeAgentAdapter(db=db)

        adapter.stop(session_id)
        flash("Session stopped.", "info")
    except Exception as e:
        flash(f"Error stopping session: {e}", "error")

    return redirect(url_for("sessions.view_session", session_id=session_id))
