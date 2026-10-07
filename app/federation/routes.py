"""Federation API: lets a *different* AgentFlow instance list and drive agent
sessions on this one (docs reference: the "master control plane" feature --
one instance's UI can show and operate projects/sessions that physically run
on another instance, so an operator only has to open one instance's UI).

Every route here requires a bearer token matching this instance's
AGENTFLOW_FEDERATION_TOKEN (app/config.py). If that config value is unset,
every route refuses the request: federation is opt-in per instance, off by
default. This is the only part of AgentFlow that gets its own auth check --
the rest of the app has none by design (loopback-only bind,
docs/CLOUDCLI_GAP_ANALYSIS.md G11) -- because making an instance reachable
by another one at all already requires AGENTFLOW_ALLOW_UNSAFE_BIND=1 and a
private network; this token is defence in depth on top of that, not a
replacement for it.

Route handlers intentionally reuse the same helpers the local sessions UI
uses (app.sessions.routes._adapter_for, app.agents.models, the composer
module) rather than re-implementing session lifecycle logic -- see
app/sessions/routes.py for the HTML-rendering counterparts of create_session/
send_prompt/stream_output/stop_session this mirrors.
"""

from __future__ import annotations

import json

from flask import Blueprint, current_app, jsonify, request

from app.agents.base import AgentContext
from app.agents.claude import ClaudeAdapter
from app.agents.codex import CodexAdapter
from app.agents.fake import FakeAgentAdapter
from app.agents.models import (
    PLACEHOLDER_PROMPT,
    auto_title_session,
    get_agent_session,
    get_clarifying_question,
    is_archived,
    list_agent_sessions_for_project,
    session_title,
    session_usage,
    update_session_metadata,
)
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.knowledge import wiki_folders
from app.knowledge.wiki_folders import WikiFolderError
from app.projects import models as project_models
from app.prompts import assembler as prompt_assembler
from app.runs.models import now
from app.sessions import composer
from app.sessions.routes import _adapter_for

bp = Blueprint("federation", __name__, url_prefix="/federation/api")

FEDERATION_VERSION = "agentflow-federation-1"


@bp.before_request
def _check_token():
    token = current_app.config.get("FEDERATION_TOKEN") or ""
    if not token:
        return jsonify({"error": "Federation is not enabled on this instance"}), 503
    if request.headers.get("Authorization") != f"Bearer {token}":
        return jsonify({"error": "Unauthorized"}), 401


@bp.get("/ping")
def ping():
    return jsonify({"status": "ok", "version": FEDERATION_VERSION})


@bp.get("/projects")
def list_projects():
    db = get_db()
    projects = project_models.list_projects(db, archived=False)
    return jsonify({
        "projects": [
            {
                "id": p.id,
                "name": p.name,
                "slug": p.slug,
                "repositories": [
                    {"id": r.id, "name": r.name, "path": r.path, "is_primary": r.is_primary}
                    for r in p.repositories
                ],
            }
            for p in projects
        ]
    })


def _session_summary(db, session) -> dict:
    from app.shell import relative_age, session_state

    waiting = (
        db.execute(
            "SELECT 1 FROM agent_questions WHERE session_id = ? AND status = 'PENDING'",
            (session.id,),
        ).fetchone()
        is not None
    )
    return {
        "id": session.id,
        "title": session_title(db, session),
        "agent_type": session.agent_type,
        "status": session.status,
        "state": "waiting" if waiting else session_state(session.status),
        "age": relative_age(session.last_activity_at or session.started_at),
        "archived": is_archived(session),
    }


@bp.get("/projects/<int:project_id>/sessions")
def project_sessions(project_id: int):
    db = get_db()
    project = project_models.get_project(db, project_id)
    if project is None:
        return jsonify({"error": "Project not found"}), 404

    show_archived = request.args.get("archived") == "1"
    sessions = [
        s for s in list_agent_sessions_for_project(db, project_id) if is_archived(s) == show_archived
    ]
    sessions.sort(key=lambda s: (s.last_activity_at, s.id), reverse=True)
    return jsonify({"total": len(sessions), "sessions": [_session_summary(db, s) for s in sessions]})


@bp.post("/projects/<int:project_id>/sessions")
def create_session(project_id: int):
    db = get_db()
    project = project_models.get_project(db, project_id)
    if project is None:
        return jsonify({"error": "Project not found"}), 404
    if not project.repositories:
        return jsonify({"error": "Project has no repositories"}), 400

    payload = request.get_json(silent=True) or request.form
    agent_type = (payload.get("agent_type") or "fake").lower()
    model = (payload.get("model") or "").strip() or None
    mcp_tools = bool(payload.get("mcp_tools"))
    repo_id = payload.get("repo_id")

    if repo_id:
        repo = project_models.get_repository(db, project_id, int(repo_id))
        if repo is None:
            return jsonify({"error": "Repository not found"}), 404
    else:
        repo = next((r for r in project.repositories if r.is_primary), project.repositories[0])
    execution_target = repo.path

    try:
        execution_provider = HostExecutionProvider(
            current_app.config["DATABASE_PATH"], current_app.config["ALLOWED_PROJECT_ROOTS"]
        )
        exec_context = execution_provider.create_context(
            {"working_directory": execution_target, "environment": {}, "target": ""}
        )

        if agent_type == "codex":
            adapter = CodexAdapter(db=db, execution_provider=execution_provider)
        elif agent_type == "claude":
            adapter = ClaudeAdapter(db=db, execution_provider=execution_provider)
        else:
            adapter = FakeAgentAdapter(db=db)

        context = AgentContext(
            project_id=project_id,
            working_directory=execution_target,
            execution_provider="host",
            execution_target=str(exec_context.id),
            model=model if agent_type in ("codex", "claude") else None,
        )

        skills_text, skill_names = prompt_assembler.skill_context(
            db, role="GENERAL", agent_type=agent_type, project_id=project_id,
        )
        initial_prompt = f"{skills_text}\n\n{PLACEHOLDER_PROMPT}" if skills_text else PLACEHOLDER_PROMPT

        start_options = {"mcp_tools": mcp_tools}
        if agent_type == "fake":
            # Only reachable in tests: the fake adapter needs a scripted plan
            # to do anything, and nothing in the real UI offers to start one
            # remotely -- this mirrors FakeAgentAdapter's own contract
            # (app/agents/fake.py) for the federation e2e test to drive.
            start_options["script"] = payload.get("script", [])
        session = adapter.start(context, initial_prompt, options=start_options)

        update_session_metadata(
            db, session.id,
            injected_context={
                "role": "GENERAL", "agent_type": agent_type, "skills": skill_names,
                "mcp_tools": mcp_tools, "assembled_at": now(),
            },
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400

    return jsonify({"id": session.id}), 201


def _session_detail(db, session) -> dict:
    adapter = _adapter_for(session)
    return {
        "id": session.id,
        "project_id": session.project_id,
        "agent_type": session.agent_type,
        "status": session.status,
        "title": session_title(db, session),
        "archived": is_archived(session),
        "usage": session_usage(session),
        "capabilities": sorted(adapter.capabilities()),
    }


@bp.get("/sessions/<int:session_id>")
def get_session(session_id: int):
    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(_session_detail(db, session))


@bp.post("/sessions/<int:session_id>/send")
def send_prompt(session_id: int):
    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    payload = request.get_json(silent=True) or request.form
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400

    try:
        _adapter_for(session).send(session_id, prompt)
        auto_title_session(db, session_id, prompt)
        return jsonify({"status": "sent"}), 200
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400


@bp.get("/sessions/<int:session_id>/stream")
def stream_output(session_id: int):
    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    after_id = request.args.get("after_id", type=int, default=0)
    adapter = _adapter_for(session)

    events = adapter.stream(session_id, after_id=after_id)
    if composer.has_due(db, session_id):
        composer.dispatch_due(db, _adapter_for, session_id)
        events = adapter.stream(session_id, after_id=after_id)

    payloads = []
    for event in events:
        data = event.data
        if event.event_type == "ClarifyingQuestion":
            question = get_clarifying_question(db, json.loads(event.data)["question_id"])
            if question is not None:
                data = json.dumps(question.to_dict())
        payloads.append(
            {"id": event.id, "event_type": event.event_type, "data": data, "created_at": event.created_at}
        )

    return jsonify({"events": payloads})


# Folder-backed wiki on this instance (app/knowledge/wiki_folders.py): every
# path is checked against *this* instance's ALLOWED_PROJECT_ROOTS, so a master
# can only reach folders this instance's operator has already allowed.

@bp.post("/wiki/setup")
def wiki_setup():
    payload = request.get_json(silent=True) or {}
    try:
        path = wiki_folders.setup_folder(
            payload.get("path") or "", current_app.config["ALLOWED_PROJECT_ROOTS"],
            create=bool(payload.get("create")), name=payload.get("name") or "Wiki",
        )
    except WikiFolderError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"path": path})


@bp.get("/wiki/pages")
def wiki_pages():
    try:
        pages = wiki_folders.list_pages(request.args.get("path", ""), current_app.config["ALLOWED_PROJECT_ROOTS"])
    except WikiFolderError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"pages": pages})


@bp.get("/wiki/page")
def wiki_page():
    try:
        content = wiki_folders.read_page(
            request.args.get("path", ""), current_app.config["ALLOWED_PROJECT_ROOTS"], request.args.get("page", ""),
        )
    except WikiFolderError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"content": content})


@bp.get("/wiki/search")
def wiki_search():
    try:
        hits = wiki_folders.search_pages(
            request.args.get("path", ""), current_app.config["ALLOWED_PROJECT_ROOTS"],
            request.args.get("q", ""), limit=request.args.get("limit", 20, type=int),
        )
    except WikiFolderError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"hits": hits})


@bp.post("/wiki/page")
def wiki_write_page():
    payload = request.get_json(silent=True) or {}
    try:
        page = wiki_folders.write_page(
            payload.get("path") or "", current_app.config["ALLOWED_PROJECT_ROOTS"],
            payload.get("page") or "", payload.get("content") or "",
            redact_patterns=tuple(current_app.config.get("REDACT_PATTERNS", ())),
        )
    except WikiFolderError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"page": page})


@bp.post("/sessions/<int:session_id>/stop")
def stop_session(session_id: int):
    db = get_db()
    session = get_agent_session(db, session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    try:
        _adapter_for(session).stop(session_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "stopped"})
