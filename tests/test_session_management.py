import os

import pytest

from app.agents import models
from app.agents.codex import CodexAdapter
from app.db import get_db
from app.projects import models as project_models


@pytest.fixture
def project_id(app):
    with app.app_context():
        db = get_db()
        path = os.path.join(app.config["allowed_root"], "repo")
        os.makedirs(path, exist_ok=True)
        pid = project_models.create_project(db, "Proj")
        project_models.add_repository(
            db, pid, "main", path, app.config["ALLOWED_PROJECT_ROOTS"], is_primary=True
        )
        return pid


def _session(app, project_id, agent="fake", status="COMPLETED", **metadata):
    with app.app_context():
        db = get_db()
        sid = models.create_agent_session(db, project_id, agent, metadata=metadata)
        models.set_session_status(db, sid, status)
        return sid


def _meta(app, sid):
    with app.app_context():
        return models.get_agent_session(get_db(), sid).metadata


def test_derive_title_takes_first_line_and_truncates():
    assert models.derive_title("\n  Fix the  login bug \nmore") == "Fix the login bug"
    assert len(models.derive_title("x" * 200)) == models.SESSION_TITLE_MAX
    assert models.derive_title("   ") == ""


def test_auto_title_from_first_real_prompt_only(app, project_id):
    sid = _session(app, project_id)
    with app.app_context():
        db = get_db()
        models.auto_title_session(db, sid, models.PLACEHOLDER_PROMPT)
        assert "title" not in models.get_agent_session(db, sid).metadata
        models.auto_title_session(db, sid, "Refactor the parser")
        models.auto_title_session(db, sid, "Something else")
        assert models.get_agent_session(db, sid).metadata["title"] == "Refactor the parser"


def test_title_falls_back_to_first_prompt_event(app, project_id):
    sid = _session(app, project_id)
    with app.app_context():
        db = get_db()
        models.add_agent_event(db, sid, "PromptSubmitted", models.PLACEHOLDER_PROMPT)
        models.add_agent_event(db, sid, "PromptSubmitted", "Write docs")
        assert models.session_title(db, models.get_agent_session(db, sid)) == "Write docs"


def test_send_prompt_auto_titles(client, app, project_id):
    sid = _session(app, project_id)
    resp = client.post(f"/sessions/{sid}/send", data={"prompt": "Add pagination"})
    assert resp.status_code == 200
    assert _meta(app, sid)["title"] == "Add pagination"


def test_rename_and_clear(client, app, project_id):
    sid = _session(app, project_id)
    resp = client.post(
        f"/sessions/{sid}/rename", data={"title": "My work"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.get_json()["title"] == "My work"
    assert _meta(app, sid)["title"] == "My work"
    client.post(f"/sessions/{sid}/rename", data={"title": ""})
    assert "title" not in _meta(app, sid)


def test_rename_unknown_session_404(client):
    resp = client.post("/sessions/999/rename", data={"title": "x"},
                       headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 404


def test_archive_restore_and_listing(client, app, project_id):
    sid = _session(app, project_id)
    ajax = {"X-Requested-With": "XMLHttpRequest"}
    assert client.post(f"/sessions/{sid}/archive", headers=ajax).get_json()["status"] == "archived"
    page = client.get(f"/sessions/project/{project_id}").data
    assert f"/sessions/{sid}\"".encode() not in page
    page = client.get(f"/sessions/project/{project_id}?archived=1").data
    assert f"/sessions/{sid}\"".encode() in page
    client.post(f"/sessions/{sid}/restore", headers=ajax)
    assert "archived_at" not in _meta(app, sid)


def test_cannot_archive_running_session(client, app, project_id):
    sid = _session(app, project_id, status="RUNNING")
    resp = client.post(f"/sessions/{sid}/archive", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 400
    assert "archived_at" not in _meta(app, sid)


def test_archived_hidden_from_sidebar(client, app, project_id):
    sid = _session(app, project_id, title="Sidebar visible")
    assert b"Sidebar visible" in client.get("/sessions").data
    client.post(f"/sessions/{sid}/archive")
    assert b"Sidebar visible" not in client.get("/sessions").data


def test_fork_copies_history_and_drops_turn_state(client, app, project_id):
    sid = _session(app, project_id, title="Original", process_id=5, turn_finalized=True, model="m1")
    with app.app_context():
        db = get_db()
        models.add_agent_event(db, sid, "PromptSubmitted", "hello")
        models.add_agent_event(db, sid, "AgentText", "hi")
    resp = client.post(f"/sessions/{sid}/fork", headers={"X-Requested-With": "XMLHttpRequest"})
    new_id = resp.get_json()["session_id"]
    meta = _meta(app, new_id)
    assert meta["forked_from"] == sid
    assert meta["title"] == "Fork of Original"
    assert meta["model"] == "m1"
    assert "process_id" not in meta and "turn_finalized" not in meta
    with app.app_context():
        events = models.list_agent_events(get_db(), new_id)
    assert [e.data for e in events] == ["hello", "hi"]


def test_fork_rejected_without_capability(client, app, project_id):
    sid = _session(app, project_id, agent="codex")
    resp = client.post(f"/sessions/{sid}/fork", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 400


def test_switch_model_capability_gated(client, app, project_id):
    ajax = {"X-Requested-With": "XMLHttpRequest"}
    codex = _session(app, project_id, agent="codex", model="a")
    assert client.post(f"/sessions/{codex}/model", data={"model": "b"}, headers=ajax).status_code == 200
    assert _meta(app, codex)["model"] == "b"
    fake = _session(app, project_id)
    assert client.post(f"/sessions/{fake}/model", data={"model": "b"}, headers=ajax).status_code == 400


def test_search_titles_messages_and_project_scope(client, app, project_id):
    a = _session(app, project_id, title="Database migration")
    b = _session(app, project_id)
    with app.app_context():
        db = get_db()
        models.add_agent_event(db, b, "AgentText", "We should update the Kubernetes manifest today")
        other = project_models.create_project(db, "Other")
        c = models.create_agent_session(db, other, "fake", metadata={"title": "Kubernetes notes"})
        assert [s.id for s, _ in models.search_sessions(db, "database")] == [a]
        hits = {s.id: snip for s, snip in models.search_sessions(db, "kubernetes")}
        assert set(hits) == {b, c}
        assert "Kubernetes manifest" in hits[b]
        assert [s.id for s, _ in models.search_sessions(db, "kubernetes", project_id=other)] == [c]
        assert models.search_sessions(db, "  ") == []
    page = client.get("/sessions?q=kubernetes").data
    assert b"Kubernetes notes" in page


def test_running_and_recent_views(client, app, project_id):
    _session(app, project_id, status="RUNNING", title="Busy one")
    _session(app, project_id, status="COMPLETED", title="Done one")
    running = client.get("/sessions?view=running").data.split(b"<main")[1]  # sidebar lists all
    assert b"Busy one" in running and b"Done one" not in running
    recent = client.get("/sessions?view=recent").data.split(b"<main")[1]
    assert b"Busy one" in recent and b"Done one" in recent


def test_codex_usage_accumulates():
    meta = {}
    CodexAdapter._record_usage(meta, {"input_tokens": 10, "output_tokens": 2, "note": "x"})
    CodexAdapter._record_usage(meta, {"input_tokens": 5, "output_tokens": 1})
    CodexAdapter._record_usage(meta, None)
    assert meta["usage"] == {"input_tokens": 15, "output_tokens": 3}


def test_chat_page_shows_usage_and_controls(client, app, project_id):
    sid = _session(app, project_id, agent="codex", usage={"input_tokens": 7, "output_tokens": 3})
    page = client.get(f"/sessions/{sid}").data
    assert b"Tokens: 7 in / 3 out" in page
    assert b'id="modelSwitch"' in page
    assert b"Fork" not in page
