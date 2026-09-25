from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for, jsonify, stream_with_context

from app.db import get_db
from app.agents.models import (
    auto_title_session,
    fork_session,
    is_archived,
    list_sessions as list_sessions_across,
    rename_session as rename_session_title,
    search_sessions,
    session_title,
    session_usage,
    set_session_archived,
    update_session_metadata,
    create_agent_session,
    delete_agent_session,
    answer_clarifying_question,
    get_agent_session,
    get_clarifying_question,
    list_agent_sessions_for_project,
    skip_clarifying_question,
)
from app.agents.codex import CodexAdapter
from app.agents.fake import FakeAgentAdapter
from app.notifications import models as notification_models
from app.projects import models as project_models
from app.settings import models as settings_models

bp = Blueprint("sessions", __name__, url_prefix="/sessions")


@bp.get("")
def list_sessions():
    db = get_db()
    projects = project_models.list_projects(db)
    names = {p.id: p.name for p in projects}
    query = request.args.get("q", "").strip()
    view = request.args.get("view", "recent")
    if view not in ("recent", "running", "archived"):
        view = "recent"

    hits = []
    if query:
        hits = [
            _row(db, s, names, snippet)
            for s, snippet in search_sessions(db, query, include_archived=view == "archived")
        ]
    rows = [
        _row(db, s, names)
        for s in list_sessions_across(
            db, archived=view == "archived", running_only=view == "running", limit=50
        )
    ]
    return render_template(
        "sessions/list.html", projects=projects, rows=rows, hits=hits, query=query, view=view
    )


def _row(db, session, project_names, snippet: str = "") -> dict:
    return {
        "session": session,
        "title": session_title(db, session),
        "project_name": project_names.get(session.project_id, ""),
        "snippet": snippet,
        "usage": session_usage(session),
    }


@bp.get("/project/<int:project_id>")
def project_sessions(project_id: int):
    db = get_db()
    project = project_models.get_project(db, project_id)
    if project is None:
        return render_template("404.html"), 404

    show_archived = request.args.get("archived") == "1"
    sessions = [
        s for s in list_agent_sessions_for_project(db, project_id) if is_archived(s) == show_archived
    ]
    sessions.sort(key=lambda s: (s.last_activity_at, s.id), reverse=True)
    titles = {s.id: session_title(db, s) for s in sessions}
    query = request.args.get("q", "").strip()
    hits = [
        _row(db, s, {project.id: project.name}, snippet)
        for s, snippet in search_sessions(
            db, query, project_id=project_id, include_archived=show_archived
        )
    ]
    openai_models = settings_models.list_enabled_models(db, provider="openai")
    return render_template(
        "sessions/project.html",
        project=project,
        sessions=sessions,
        titles=titles,
        show_archived=show_archived,
        query=query,
        hits=hits,
        openai_models=openai_models,
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
    model = request.form.get("model", "").strip() or None

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
            model=model if agent_type == "codex" else None,
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

    capabilities = _adapter_for(session).capabilities()
    return render_template(
        "sessions/chat.html",
        session=session,
        project=project,
        repo=repo,
        title=session_title(db, session),
        archived=is_archived(session),
        usage=session_usage(session),
        can_fork="fork" in capabilities,
        can_switch_model="model_selection" in capabilities,
        model_options=settings_models.list_enabled_models(db, provider="openai"),
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
        auto_title_session(db, session_id, prompt)
        return jsonify({"status": "sent"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/<int:session_id>/stream")
def stream_output(session_id: int):
    import json

    from app.execution.host import HostExecutionProvider

    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        return {"error": "Session not found"}, 404

    after_id = request.args.get("after_id", type=int, default=0)

    if session.agent_type.lower() == "codex":
        execution_provider = HostExecutionProvider(
            current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
        )
        adapter = CodexAdapter(db=db, execution_provider=execution_provider)
    else:
        adapter = FakeAgentAdapter(db=db)

    # adapter.stream() runs _sync_events() first, translating any output the
    # background process has produced since the last poll into AgentEvents —
    # reading list_agent_events() directly here would skip that and the
    # frontend would never see a send()-triggered turn's reply land.
    events = adapter.stream(session_id, after_id=after_id)

    # Resolve question state now: generate() runs after the request's DB
    # connection has been closed.
    payloads = []
    for event in events:
        data = event.data
        if event.event_type == "ClarifyingQuestion":
            # Send the question's current state, not just its id, so the page
            # can render options and show answered/skipped on reload.
            question = get_clarifying_question(db, json.loads(event.data)["question_id"])
            if question is not None:
                data = json.dumps(question.to_dict())
        payloads.append({
            "id": event.id,
            "event_type": event.event_type,
            "data": data,
            "created_at": event.created_at,
        })

    def generate():
        for payload in payloads:
            yield f"data: {json.dumps(payload)}\n\n"

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


def _adapter_for(session):
    from app.execution.host import HostExecutionProvider

    if session.agent_type.lower() == "codex":
        execution_provider = HostExecutionProvider(
            current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
        )
        return CodexAdapter(db=get_db(), execution_provider=execution_provider)
    return FakeAgentAdapter(db=get_db())


@bp.post("/<int:session_id>/questions/<int:question_id>/answer")
def answer_question(session_id: int, question_id: int):
    """Answer or skip a pending clarifying question.

    Form fields: ``selected`` (repeatable option label), ``other_text``, or
    ``skip=1``. A skip is recorded but nothing is sent: the agent waits.
    """
    db = get_db()
    session = get_agent_session(db, session_id)
    question = get_clarifying_question(db, question_id)
    if session is None or question is None or question.session_id != session_id:
        return jsonify({"error": "Question not found"}), 404

    try:
        if request.form.get("skip"):
            question = skip_clarifying_question(db, question_id)
        else:
            question = answer_clarifying_question(
                db,
                question_id,
                selected=request.form.getlist("selected"),
                other_text=request.form.get("other_text", ""),
            )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # The blocker is resolved (answered or skipped), so stop nagging.
    notification_models.mark_session_read(db, session_id)

    if question.status == "ANSWERED":
        try:
            _adapter_for(session).send(session_id, question.answer_text())
        except Exception as e:
            return jsonify({"error": f"Answer saved but not delivered: {e}"}), 502

    return jsonify({"status": question.status.lower(), "question": question.to_dict()}), 200


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@bp.post("/<int:session_id>/delete")
def delete_session(session_id: int):
    from app.execution.host import HostExecutionProvider

    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        if _wants_json():
            return jsonify({"error": "Session not found"}), 404
        return redirect(url_for("sessions.list_sessions"))

    project_id = session.project_id

    if session.status in ("RUNNING", "STARTING"):
        try:
            if session.agent_type.lower() == "codex":
                execution_provider = HostExecutionProvider(
                    current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
                )
                adapter = CodexAdapter(db=db, execution_provider=execution_provider)
            else:
                adapter = FakeAgentAdapter(db=db)
            adapter.stop(session_id)
        except Exception:
            pass

    delete_agent_session(db, session_id)

    if _wants_json():
        return jsonify({"status": "deleted", "message": "Session deleted."}), 200

    flash("Session deleted.", "info")
    return redirect(url_for("sessions.project_sessions", project_id=project_id))


def _session_action_response(session_id: int, payload: dict, redirect_to: str | None = None):
    """JSON for AJAX callers, otherwise a flash + redirect (non-JS fallback)."""
    if _wants_json():
        return jsonify(payload), 200
    flash(payload.get("message", "Done."), "info")
    return redirect(redirect_to or url_for("sessions.view_session", session_id=session_id))


def _session_or_404(session_id: int):
    session = get_agent_session(get_db(), session_id)
    if session is None:
        if _wants_json():
            return None, (jsonify({"error": "Session not found"}), 404)
        return None, (render_template("404.html"), 404)
    return session, None


@bp.post("/<int:session_id>/rename")
def rename_session(session_id: int):
    session, error = _session_or_404(session_id)
    if error:
        return error
    db = get_db()
    title = rename_session_title(db, session_id, request.form.get("title", ""))
    shown = title or session_title(db, get_agent_session(db, session_id))
    return _session_action_response(
        session_id, {"status": "renamed", "title": shown, "message": "Session renamed."}
    )


@bp.post("/<int:session_id>/archive")
def archive_session(session_id: int):
    session, error = _session_or_404(session_id)
    if error:
        return error
    try:
        set_session_archived(get_db(), session_id, True)
    except ValueError as e:
        if _wants_json():
            return jsonify({"error": str(e)}), 400
        flash(str(e), "error")
        return redirect(url_for("sessions.view_session", session_id=session_id))
    return _session_action_response(
        session_id,
        {"status": "archived", "message": "Session archived."},
        url_for("sessions.project_sessions", project_id=session.project_id),
    )


@bp.post("/<int:session_id>/restore")
def restore_session(session_id: int):
    session, error = _session_or_404(session_id)
    if error:
        return error
    set_session_archived(get_db(), session_id, False)
    return _session_action_response(
        session_id,
        {"status": "restored", "message": "Session restored."},
        url_for("sessions.project_sessions", project_id=session.project_id),
    )


@bp.post("/<int:session_id>/fork")
def fork(session_id: int):
    session, error = _session_or_404(session_id)
    if error:
        return error
    if "fork" not in _adapter_for(session).capabilities():
        message = f"{session.agent_type} sessions cannot be forked."
        if _wants_json():
            return jsonify({"error": message}), 400
        flash(message, "error")
        return redirect(url_for("sessions.view_session", session_id=session_id))
    new_id = fork_session(get_db(), session_id)
    url = url_for("sessions.view_session", session_id=new_id)
    return _session_action_response(
        new_id, {"status": "forked", "session_id": new_id, "url": url, "message": "Session forked."}, url
    )


@bp.post("/<int:session_id>/model")
def switch_model(session_id: int):
    """Change the model used for the session's next turn (capability-gated)."""
    session, error = _session_or_404(session_id)
    if error:
        return error
    if "model_selection" not in _adapter_for(session).capabilities():
        return jsonify({"error": f"{session.agent_type} does not support switching models."}), 400
    model = request.form.get("model", "").strip() or None
    update_session_metadata(get_db(), session_id, model=model)
    return jsonify({"status": "updated", "model": model, "message": "Model updated for the next message."}), 200
