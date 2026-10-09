from __future__ import annotations

import json

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
    set_session_topic,
    update_session_metadata,
    create_agent_session,
    delete_agent_session,
    answer_clarifying_question,
    get_agent_session,
    get_clarifying_question,
    list_agent_sessions_for_project,
    skip_clarifying_question,
)
from app.agents.claude import PERMISSION_MODES as CLAUDE_PERMISSION_MODES, ClaudeAdapter
from app.agents.codex import PERMISSION_MODES as CODEX_PERMISSION_MODES, CodexAdapter
from app.sessions import composer, starter, topics as topic_models
from app.agents.fake import FakeAgentAdapter
from app.knowledge import wiki_sources
from app.notifications import models as notification_models
from app.projects import models as project_models
from app.settings import models as settings_models

bp = Blueprint("sessions", __name__, url_prefix="/sessions")

# Sandbox/permission modes and catalog providers differ per agent CLI.
_PERMISSION_MODES_BY_AGENT = {"codex": CODEX_PERMISSION_MODES, "claude": CLAUDE_PERMISSION_MODES}
_MODEL_PROVIDERS_BY_AGENT = {"codex": ("openai", "local"), "claude": ("anthropic", "local")}


def _permission_modes_for(agent_type: str) -> tuple[str, ...]:
    return _PERMISSION_MODES_BY_AGENT.get((agent_type or "").lower(), ())


def _model_options_for(db, agent_type: str) -> list[settings_models.ModelCatalogEntry]:
    options: list[settings_models.ModelCatalogEntry] = []
    for provider in _MODEL_PROVIDERS_BY_AGENT.get((agent_type or "").lower(), ()):
        options.extend(settings_models.list_enabled_models(db, provider=provider))
    return options


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
    topic_names = topic_models.topic_names(db, [r["session"].topic_id for r in rows + hits])
    return render_template(
        "sessions/list.html", projects=projects, rows=rows, hits=hits, query=query, view=view,
        topic_names=topic_names,
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
    # All providers relevant to any startable agent type; project.html filters
    # the options shown by the currently selected agent_type client-side.
    model_options = settings_models.list_enabled_models(
        db, provider="openai"
    ) + settings_models.list_enabled_models(
        db, provider="anthropic"
    ) + settings_models.list_enabled_models(db, provider="local")
    return render_template(
        "sessions/project.html",
        project=project,
        sessions=sessions,
        titles=titles,
        show_archived=show_archived,
        query=query,
        hits=hits,
        model_options=model_options,
        model_providers_by_agent=_MODEL_PROVIDERS_BY_AGENT,
        warm_start_default=current_app.config.get("SESSION_WARM_START_DEFAULT", False),
        project_wikis=wiki_sources.list_for_project(db, project_id),
        topics=topic_models.list_topics(db, project_id),
        selected_topic=request.args.get("topic", type=int),
        topic_names=topic_models.topic_names(db, [s.topic_id for s in sessions]),
    )


@bp.post("/project/<int:project_id>/create")
def create_session(project_id: int):
    db = get_db()
    project = project_models.get_project(db, project_id)
    if project is None:
        return render_template("404.html"), 404

    if not project.repositories:
        flash("Project has no repositories. Add one before starting a session.", "error")
        return redirect(url_for("sessions.project_sessions", project_id=project_id))

    agent_type = request.form.get("agent_type", "codex").lower()
    model = request.form.get("model", "").strip() or None
    mcp_tools = request.form.get("mcp_tools") == "1"
    try:
        topic_id = topic_models.resolve_topic_choice(
            db, project_id, request.form.get("topic", ""), request.form.get("new_topic_name", "")
        )
    except topic_models.TopicError as e:
        flash(str(e), "error")
        return redirect(url_for("sessions.project_sessions", project_id=project_id))

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
        session_id = starter.start_interactive_session(
            current_app.config, db, project_id, execution_target, agent_type,
            model=model, mcp_tools=mcp_tools,
            warm_start=request.form.get("warm_start") == "1",
            wiki_context=request.form.get("wiki_context") == "1",
            topic_id=topic_id,
        )
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
    from app.backlog import discussion, persistence as backlog

    item_id = discussion.session_item_id(session)
    discussed_item = backlog.get_item(db, item_id) if item_id else None
    return render_template(
        "sessions/chat.html",
        discussed_item=discussed_item,
        topic=topic_models.get_topic(db, session.topic_id) if session.topic_id else None,
        topics=topic_models.list_topics(db, session.project_id),
        session=session,
        project=project,
        repo=repo,
        title=session_title(db, session),
        archived=is_archived(session),
        usage=session_usage(session),
        can_fork="fork" in capabilities,
        can_switch_model="model_selection" in capabilities,
        model_options=_model_options_for(db, session.agent_type),
    )


@bp.post("/<int:session_id>/send")
def send_prompt(session_id: int):
    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    prompt = request.form.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400

    try:
        attachments = composer.resolve_attachments(
            current_app.config["DATABASE_PATH"], session_id, request.form.getlist("attachment")
        )
    except composer.ComposerError as e:
        return jsonify({"error": str(e)}), 400
    prompt, images = composer.compose_prompt(prompt, attachments)

    try:
        adapter = _adapter_for(session)
        if images and "image_input" in adapter.capabilities():
            adapter.send(session_id, prompt, options={"images": images})
        else:
            adapter.send(session_id, prompt)
        auto_title_session(db, session_id, request.form.get("prompt", ""))
        return jsonify({"status": "sent"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/<int:session_id>/stream")
def stream_output(session_id: int):
    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        return {"error": "Session not found"}, 404

    after_id = request.args.get("after_id", type=int, default=0)

    adapter = _adapter_for(session)

    # adapter.stream() runs _sync_events() first, translating any output the
    # background process has produced since the last poll into AgentEvents —
    # reading list_agent_events() directly here would skip that and the
    # frontend would never see a send()-triggered turn's reply land.
    events = adapter.stream(session_id, after_id=after_id)
    if composer.has_due(db, session_id):
        # The stream() above synced the session's status, so a finished turn
        # no longer blocks delivery; re-read events to include the new prompt.
        composer.dispatch_due(db, _adapter_for, session_id)
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
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        if is_ajax:
            return jsonify({"error": "Session not found"}), 404
        return redirect(url_for("sessions.list_sessions"))

    try:
        adapter = _adapter_for(session)
        adapter.stop(session_id)
    except Exception as e:
        if is_ajax:
            return jsonify({"error": str(e)}), 400
        flash(f"Error stopping session: {e}", "error")
        return redirect(url_for("sessions.view_session", session_id=session_id))

    if is_ajax:
        return jsonify({"status": "stopped", "message": "Session stopped."})
    flash("Session stopped.", "info")
    return redirect(url_for("sessions.view_session", session_id=session_id))


def _adapter_for(session):
    from app.execution.host import HostExecutionProvider

    agent_type = session.agent_type.lower()
    if agent_type in ("codex", "claude"):
        execution_provider = HostExecutionProvider(
            current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
        )
        adapter_cls = ClaudeAdapter if agent_type == "claude" else CodexAdapter
        return adapter_cls(db=get_db(), execution_provider=execution_provider)
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
    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        if _wants_json():
            return jsonify({"error": "Session not found"}), 404
        return redirect(url_for("sessions.list_sessions"))

    project_id = session.project_id

    if session.status in ("RUNNING", "STARTING"):
        try:
            _adapter_for(session).stop(session_id)
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
    topic_models.summarize_on_archive(current_app.config, get_db(), session_id)
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


@bp.post("/<int:session_id>/topic")
def assign_topic(session_id: int):
    """Put the session in a Topic -- an existing one, a "new" one named in
    `new_topic_name`, or none (empty `topic`) -- at any point in the
    conversation (docs/SESSION_TOPICS.md §5.2). Only sets topic_id; the
    rolling summary picks the session up when it is next archived."""
    session, error = _session_or_404(session_id)
    if error:
        return error
    db = get_db()
    try:
        topic_id = topic_models.resolve_topic_choice(
            db, session.project_id, request.form.get("topic", ""), request.form.get("new_topic_name", "")
        )
    except topic_models.TopicError as e:
        if _wants_json():
            return jsonify({"error": str(e)}), 400
        flash(str(e), "error")
        return redirect(url_for("sessions.view_session", session_id=session_id))
    set_session_topic(db, session_id, topic_id)
    topic = topic_models.get_topic(db, topic_id) if topic_id else None
    payload = {
        "status": "updated",
        "topic_id": topic_id,
        "topic_name": topic.name if topic else None,
        "message": f"Session added to topic {topic.name}." if topic else "Session removed from its topic.",
    }
    return _session_action_response(session_id, payload)


# Chat events that are conversation (not tool chatter) and may seed a Backlog item.
_CAPTURABLE_EVENTS = ("PromptSubmitted", "AgentText", "ClarifyingQuestion")
CAPTURE_TEXT_MAX = 8000


@bp.post("/<int:session_id>/backlog-item")
def capture_backlog_item(session_id: int):
    """Create an INBOX Backlog item from chat messages (task 57): `event_id`
    (repeatable) names the selected messages; `title`/`description` default
    to the first message's first line / the messages themselves. The item
    records the session as its source and links back to it."""
    from app.agents.models import derive_title, list_agent_events
    from app.backlog import persistence as backlog

    session, error = _session_or_404(session_id)
    if error:
        return error
    db = get_db()

    def fail(message: str, status: int = 400):
        if _wants_json():
            return jsonify({"status": "error", "error": message}), status
        flash(message, "error")
        return redirect(url_for("sessions.view_session", session_id=session_id))

    wanted = []
    for raw in request.form.getlist("event_id"):
        if not raw.strip().isdigit():
            return fail("Invalid message id.")
        wanted.append(int(raw))
    events = {e.id: e for e in list_agent_events(db, session_id) if e.event_type in _CAPTURABLE_EVENTS}
    missing = [i for i in wanted if i not in events]
    if missing:
        return fail("Some selected messages are not part of this session.", 404)
    selected = [events[i] for i in sorted(set(wanted))]

    def message_text(event) -> str:
        if event.event_type == "ClarifyingQuestion":
            question = get_clarifying_question(db, json.loads(event.data).get("question_id", 0))
            return question.question if question else ""
        return event.data

    roles = {"PromptSubmitted": "Developer", "AgentText": "Agent", "ClarifyingQuestion": "Agent (question)"}
    description = request.form.get("description", "").strip()
    if not description:
        description = "\n\n".join(f"{roles[e.event_type]}: {message_text(e).strip()}" for e in selected)
    if len(description) > CAPTURE_TEXT_MAX:
        description = description[:CAPTURE_TEXT_MAX].rstrip() + "\n[...truncated]"
    title = request.form.get("title", "").strip()
    if not title and selected:
        title = derive_title(message_text(selected[0]).strip().splitlines()[0] if message_text(selected[0]).strip() else "")
    if not title and not description:
        return fail("Select a message or enter a title or description.")

    session_url = url_for("sessions.view_session", session_id=session_id)
    try:
        item_id = backlog.create_item(
            db, session.project_id, text=description, title=title[:120],
            priority=request.form.get("priority") or None, created_by="chat",
            source_type="chat_session", source_reference=str(session_id),
        )
    except ValueError as exc:
        return fail(str(exc))
    backlog.add_attachment(db, item_id, "LINK", f"Chat session #{session_id}", session_url)
    if selected:
        backlog.record_note(
            db, item_id, "captured from chat session #%d (messages %s)" % (session_id, ", ".join(str(e.id) for e in selected)),
            "chat",
        )
    item_url = url_for("backlog.view_item", project_id=session.project_id, item_id=item_id)
    if _wants_json():
        return jsonify({"status": "success", "message": f"Backlog item #{item_id} created.",
                        "backlog_item_id": item_id, "url": item_url}), 201
    flash(f"Backlog item #{item_id} created.", "info")
    return redirect(session_url)


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


def _composer_session(session_id: int):
    session = get_agent_session(get_db(), session_id)
    if session is None:
        return None, (jsonify({"error": "Session not found"}), 404)
    return session, None


@bp.get("/<int:session_id>/composer")
def composer_config(session_id: int):
    """Everything the prompt bar needs: commands, permission mode, schedule."""
    session, error = _composer_session(session_id)
    if error:
        return error
    capabilities = _adapter_for(session).capabilities()
    permission_modes = _permission_modes_for(session.agent_type)
    return jsonify({
        "commands": [
            {"name": c.name, "description": c.description, "args": c.args}
            for c in composer.available_commands(capabilities, permission_modes)
        ],
        "permission_modes": list(permission_modes) if "permission_modes" in capabilities else [],
        "permission_mode": session.metadata.get("permission_mode") or "",
        "attachments": True,
        "images": "image_input" in capabilities,
        "scheduled": composer.list_scheduled(get_db(), session_id),
    })


@bp.get("/<int:session_id>/mentions")
def mention_search(session_id: int):
    session, error = _composer_session(session_id)
    if error:
        return error
    directory = session.metadata.get("working_directory", "")
    paths = composer.search_mentions(
        directory, current_app.config["ALLOWED_PROJECT_ROOTS"], request.args.get("q", "")
    ) if directory else []
    return jsonify({"paths": paths})


@bp.post("/<int:session_id>/attachments")
def upload_attachment(session_id: int):
    session, error = _composer_session(session_id)
    if error:
        return error
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "Choose a file to attach."}), 400
    try:
        saved = composer.save_attachment(
            current_app.config["DATABASE_PATH"], session_id, upload.filename, upload.stream
        )
    except composer.ComposerError as e:
        return jsonify({"error": str(e)}), 413 if "larger" in str(e) else 400
    from app.backlog import discussion

    item_id = discussion.session_item_id(session)
    if item_id:
        # Files shared in an item discussion also become the item's attachments (task 58).
        saved["backlog_attachment_id"] = discussion.attach_upload(
            current_app.config, get_db(), session, item_id, saved["id"], saved["name"],
        )
    return jsonify(saved), 201


@bp.post("/<int:session_id>/command")
def run_command(session_id: int):
    """Execute a slash command typed in the prompt bar."""
    session, error = _composer_session(session_id)
    if error:
        return error
    parsed = composer.parse_command(request.form.get("command", ""))
    if parsed is None:
        return jsonify({"error": "Not a command."}), 400
    name, arg = parsed
    db = get_db()
    adapter = _adapter_for(session)
    capabilities = adapter.capabilities()
    known = {
        c.name: c
        for c in composer.available_commands(capabilities, _permission_modes_for(session.agent_type))
    }
    if name not in known:
        return jsonify({"error": f"Unknown command /{name}. Type /help for the list."}), 400

    def done(message, **extra):
        return jsonify({"status": "ok", "message": message, **extra}), 200

    def fail(message):
        return jsonify({"error": message}), 400

    if name == "help":
        lines = [f"/{c.name} {c.args}".strip() + f" - {c.description}" for c in known.values()]
        return done("\n".join(lines))
    if name == "rename":
        title = rename_session_title(db, session_id, arg)
        return done("Session renamed.", reload=True, title=title)
    if name == "model":
        if not arg:
            return fail("Usage: /model <model id> (or /model default)")
        update_session_metadata(db, session_id, model=None if arg == "default" else arg)
        return done(f"Model set to {arg} for the next message.")
    if name == "mode":
        modes = _permission_modes_for(session.agent_type)
        if arg not in modes:
            return fail("Usage: /mode <" + " | ".join(modes) + ">")
        update_session_metadata(db, session_id, permission_mode=arg)
        return done(f"Permission mode set to {arg} for the next message.", permission_mode=arg)
    if name == "usage":
        usage = session_usage(session)
        if not usage:
            return done("No token usage recorded yet.")
        return done(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in usage.items()))
    if name == "fork":
        new_id = fork_session(db, session_id)
        return done("Session forked.", redirect=url_for("sessions.view_session", session_id=new_id))
    if name == "archive":
        try:
            set_session_archived(db, session_id, True)
        except ValueError as e:
            return fail(str(e))
        topic_models.summarize_on_archive(current_app.config, db, session_id)
        return done(
            "Session archived.",
            redirect=url_for("sessions.project_sessions", project_id=session.project_id),
        )
    if name == "stop":
        try:
            adapter.stop(session_id)
        except Exception as e:  # noqa: BLE001
            return fail(str(e))
        return done("Session stopped.", reload=True)
    return fail("Unhandled command.")


@bp.post("/<int:session_id>/schedule")
def schedule_message(session_id: int):
    session, error = _composer_session(session_id)
    if error:
        return error
    db = get_db()
    try:
        message_id = composer.schedule_message(
            db, session_id, request.form.get("content", ""), request.form.get("send_at", "")
        )
    except composer.ComposerError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "scheduled", "id": message_id,
                    "scheduled": composer.list_scheduled(db, session_id)}), 201


@bp.post("/<int:session_id>/schedule/<int:message_id>/cancel")
def cancel_scheduled_message(session_id: int, message_id: int):
    session, error = _composer_session(session_id)
    if error:
        return error
    db = get_db()
    if not composer.cancel_scheduled(db, session_id, message_id):
        return jsonify({"error": "Scheduled message not found."}), 404
    return jsonify({"status": "cancelled", "scheduled": composer.list_scheduled(db, session_id)})


@bp.cli.command("dispatch-scheduled")
def dispatch_scheduled_command():
    """Send every scheduled message that is due (for cron or a systemd timer).

    Run as ``flask sessions dispatch-scheduled``.
    """
    import click

    sent = composer.dispatch_due(get_db(), _adapter_for)
    click.echo(f"Sent {sent} scheduled message(s).")
