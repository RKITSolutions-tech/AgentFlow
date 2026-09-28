"""Interactive chat sessions never called into the prompt library before this
task (docs/AGENT_ADAPTER.md §23.2/23.3); this covers the wiring in
app/sessions/routes.py that now prepends matching skills to the placeholder
starting prompt, and that this doesn't break session auto-titling."""
import os

from app.agents.models import PLACEHOLDER_PROMPT, auto_title_session, get_agent_session, list_agent_events
from app.db import get_db
from app.prompts import models as prompt_models


def _create_project(client, app, name="Skill Project"):
    allowed_root = app.config["allowed_root"]
    repo_path = os.path.join(allowed_root, name.replace(" ", "-"))
    os.makedirs(repo_path, exist_ok=True)
    client.post("/projects/new", data={"name": name, "description": "", "repo_name": "main", "repo_path": repo_path})


def _first_prompt(app, session_id):
    with app.app_context():
        events = list_agent_events(get_db(), session_id)
    return next(e.data for e in events if e.event_type == "PromptSubmitted")


def test_no_skills_defined_behaves_identically_to_before(client, app):
    _create_project(client, app)
    resp = client.post("/sessions/project/1/create", data={"agent_type": "fake"}, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        db = get_db()
        session_id = db.execute("SELECT id FROM agent_sessions ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert _first_prompt(app, session_id) == PLACEHOLDER_PROMPT


def test_matching_active_skill_is_prepended(client, app):
    with app.app_context():
        prompt_models.create_fragment(
            get_db(), "general-skill", "Always be concise.", skill_status="active",
            skill_when_to_use="Every session.",
        )
    _create_project(client, app)
    client.post("/sessions/project/1/create", data={"agent_type": "fake"}, follow_redirects=True)
    with app.app_context():
        db = get_db()
        session_id = db.execute("SELECT id FROM agent_sessions ORDER BY id DESC LIMIT 1").fetchone()[0]
    prompt = _first_prompt(app, session_id)
    assert "Always be concise." in prompt
    assert prompt.endswith(PLACEHOLDER_PROMPT)
    # Compliance/audit trail (task 53): interactive sessions never touch
    # execution_prompts, so the injected skill manifest is recorded on the
    # session's own metadata instead.
    with app.app_context():
        injected = get_agent_session(get_db(), session_id).metadata["injected_context"]
    assert injected["role"] == "GENERAL"
    assert injected["skills"] == ["general-skill"]
    assert injected["assembled_at"]


def test_non_matching_skill_is_not_injected(client, app):
    with app.app_context():
        prompt_models.create_fragment(
            get_db(), "planning-only", "Plan carefully.", skill_status="active", skill_roles=["planning"],
        )
    _create_project(client, app)
    client.post("/sessions/project/1/create", data={"agent_type": "fake"}, follow_redirects=True)
    with app.app_context():
        db = get_db()
        session_id = db.execute("SELECT id FROM agent_sessions ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert _first_prompt(app, session_id) == PLACEHOLDER_PROMPT


def test_auto_title_still_skips_placeholder_with_prepended_skill(client, app):
    """The endswith() fix: a real first prompt must still title the session,
    and a skill-prefixed placeholder must still be treated as 'no real prompt yet'."""
    _create_project(client, app)
    with app.app_context():
        db = get_db()
        from app.agents.models import create_agent_session

        session_id = create_agent_session(db, project_id=1, agent_type="fake")
        prefixed_placeholder = f"Skills:\n\n### s\nDo X.\n\n{PLACEHOLDER_PROMPT}"
        auto_title_session(db, session_id, prefixed_placeholder)
        assert get_agent_session(db, session_id).metadata.get("title") is None

        auto_title_session(db, session_id, "Please refactor the login module")
        assert get_agent_session(db, session_id).metadata.get("title")
