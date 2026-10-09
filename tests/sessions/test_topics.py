"""Session Topics (docs/SESSION_TOPICS.md, tasks 82-94): schema, topics.py CRUD
and context, session-start/Add-to-Topic wiring, rolling summary on archive and
re-compression, and the Topics pages."""
import os
import sqlite3

import pytest

from app.agents.base import AgentContext
from app.agents.fake import FakeAgentAdapter
from app.agents.models import (
    fork_session,
    get_agent_session,
    list_agent_events,
    set_session_archived,
    set_session_topic,
)
from app.db import _migrate, get_db
from app.sessions import topics

AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def db(app):
    app.config["PLANNING_AGENT"] = "fake"
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        conn.execute("INSERT INTO projects (name, slug) VALUES ('Q', 'q')")
        conn.commit()
        yield conn


def _session(db, project_id=1, texts=("hello",), topic_id=None):
    adapter = FakeAgentAdapter(db)
    session = adapter.start(
        AgentContext(project_id=project_id, working_directory="."),
        "start prompt",
        options={"role": "GENERAL", "script": [{"action": "message", "text": t} for t in texts]},
    )
    if topic_id:
        set_session_topic(db, session.id, topic_id)
    return session.id


# -- schema (tasks 82/83) -------------------------------------------------------------


def test_discussion_topics_schema(db):
    columns = {r["name"]: r for r in db.execute("PRAGMA table_info(discussion_topics)")}
    assert set(columns) == {"id", "project_id", "name", "status", "rolling_summary", "created_at", "updated_at"}
    topic_id = topics.create_topic(db, 1, "Auth rework")
    row = db.execute("SELECT * FROM discussion_topics WHERE id = ?", (topic_id,)).fetchone()
    assert row["status"] == "active" and row["rolling_summary"] == "" and row["created_at"]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO discussion_topics (project_id, name) VALUES (1, 'Auth rework')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO discussion_topics (project_id, name) VALUES (999, 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO discussion_topics (project_id, name, status) VALUES (1, 'y', 'bogus')")


def test_agent_sessions_topic_column_and_index(db):
    columns = {r["name"]: r for r in db.execute("PRAGMA table_info(agent_sessions)")}
    assert "topic_id" in columns and not columns["topic_id"]["notnull"]
    indexes = {r["name"] for r in db.execute("PRAGMA index_list(agent_sessions)")}
    assert "idx_agent_sessions_topic" in indexes
    session_id = _session(db)
    assert get_agent_session(db, session_id).topic_id is None


def test_migration_adds_topic_id_to_old_agent_sessions(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.sqlite3")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE agent_sessions (id INTEGER PRIMARY KEY, project_id INTEGER, agent_type TEXT, "
        "role TEXT, metadata TEXT)"
    )
    conn.execute("INSERT INTO agent_sessions VALUES (1, 1, 'fake', 'GENERAL', '{}')")
    _migrate(conn)
    assert "topic_id" in {r["name"] for r in conn.execute("PRAGMA table_info(agent_sessions)")}
    assert conn.execute("SELECT topic_id FROM agent_sessions").fetchone()[0] is None
    assert "idx_agent_sessions_topic" in {r["name"] for r in conn.execute("PRAGMA index_list(agent_sessions)")}


def test_deleting_topic_leaves_sessions_without_one(db):
    topic_id = topics.create_topic(db, 1, "Gone")
    session_id = _session(db, topic_id=topic_id)
    db.execute("DELETE FROM discussion_topics WHERE id = ?", (topic_id,))
    db.commit()
    assert get_agent_session(db, session_id).topic_id is None


# -- topics.py CRUD (task 84) ---------------------------------------------------------


def test_create_list_rename_archive(db):
    a = topics.create_topic(db, 1, "  Auth   rework ")
    b = topics.create_topic(db, 1, "Pipeline retries")
    topics.create_topic(db, 2, "Auth rework")  # same name, other project: fine
    assert topics.get_topic(db, a).name == "Auth rework"
    assert {t.name for t in topics.list_topics(db, 1)} == {"Auth rework", "Pipeline retries"}

    with pytest.raises(topics.TopicError):
        topics.create_topic(db, 1, "auth REWORK")  # case-insensitive duplicate
    with pytest.raises(topics.TopicError):
        topics.create_topic(db, 1, "   ")
    with pytest.raises(topics.TopicError):
        topics.create_topic(db, 1, "x" * 81)
    with pytest.raises(topics.TopicError):
        topics.rename_topic(db, b, "auth rework")

    assert topics.rename_topic(db, a, "Auth v2") == "Auth v2"
    topics.archive_topic(db, b)
    assert [t.name for t in topics.list_topics(db, 1)] == ["Auth v2"]
    assert {t.name for t in topics.list_topics(db, 1, include_archived=True)} == {"Auth v2", "Pipeline retries"}
    topics.restore_topic(db, b)
    assert len(topics.list_topics(db, 1)) == 2
    with pytest.raises(topics.TopicError):
        topics.set_topic_status(db, b, "deleted")
    with pytest.raises(topics.TopicError):
        topics.archive_topic(db, 9999)


def test_session_count_and_project_scoping(db):
    topic_id = topics.create_topic(db, 1, "Thread")
    _session(db, topic_id=topic_id)
    _session(db, topic_id=topic_id)
    _session(db)
    assert topics.get_topic(db, topic_id).session_count == 2
    assert len(topics.topic_sessions(db, topic_id)) == 2
    with pytest.raises(topics.TopicError):
        topics.get_project_topic(db, 2, topic_id)
    with pytest.raises(topics.TopicError):
        topics.resolve_topic_choice(db, 2, str(topic_id))


def test_resolve_topic_choice(db):
    existing = topics.create_topic(db, 1, "Existing")
    assert topics.resolve_topic_choice(db, 1, "") is None
    assert topics.resolve_topic_choice(db, 1, str(existing)) == existing
    created = topics.resolve_topic_choice(db, 1, "new", "Brand new")
    assert topics.get_topic(db, created).name == "Brand new"
    topics.archive_topic(db, existing)
    with pytest.raises(topics.TopicError, match="archived"):
        topics.resolve_topic_choice(db, 1, str(existing))
    with pytest.raises(topics.TopicError):
        topics.resolve_topic_choice(db, 1, "abc")


def test_topic_context_is_summary_plus_latest_session_messages(db):
    topic_id = topics.create_topic(db, 1, "Auth rework")
    assert topics.get_topic_context(db, topic_id) == ""
    _session(db, texts=("older reply",), topic_id=topic_id)
    db.execute("UPDATE agent_sessions SET last_activity_at = '2000-01-01 00:00:00'")
    _session(db, texts=("latest reply",), topic_id=topic_id)
    _session(db, texts=("unrelated project chatter",))
    db.execute("UPDATE discussion_topics SET rolling_summary = 'chose JWT over sessions'")
    db.commit()

    context = topics.get_topic_context(db, topic_id)
    assert 'topic "Auth rework"' in context
    assert "chose JWT over sessions" in context
    assert "latest reply" in context
    assert "older reply" not in context  # only the most recent session's messages
    assert "unrelated project chatter" not in context


# -- session model (task 85) ----------------------------------------------------------


def test_fork_keeps_topic_and_only_summarizes_new_messages(app, db):
    topic_id = topics.create_topic(db, 1, "Forked")
    source = _session(db, texts=("parent text",), topic_id=topic_id)
    fork_id = fork_session(db, source)
    fork = get_agent_session(db, fork_id)
    assert fork.topic_id == topic_id
    set_session_archived(db, fork_id, True)
    assert topics.append_session_summary_to_topic(db, app.config, fork_id) is None


# -- rolling summary (tasks 89/90) ----------------------------------------------------


def test_archive_appends_summary_line_once(app, db):
    topic_id = topics.create_topic(db, 1, "Summaries")
    first = _session(db, texts=("a", "b"), topic_id=topic_id)
    second = _session(db, texts=("c",), topic_id=topic_id)
    before = topics.get_topic(db, topic_id).updated_at
    db.execute("UPDATE discussion_topics SET updated_at = '2000-01-01 00:00:00'")
    db.commit()

    line = topics.append_session_summary_to_topic(db, app.config, first)
    assert line and "messages" in line and len(line) <= 72
    assert topics.append_session_summary_to_topic(db, app.config, first) is None  # nothing new
    topics.append_session_summary_to_topic(db, app.config, second)
    topic = topics.get_topic(db, topic_id)
    assert len(topic.summary_lines) == 2
    assert topic.summary_lines[0] == line  # newest last
    assert topic.updated_at > "2000-01-01 00:00:00" and before


def test_session_without_topic_is_not_summarized(app, db):
    session_id = _session(db)
    assert topics.append_session_summary_to_topic(db, app.config, session_id) is None


def test_rolling_summary_is_recompressed_past_cap(app, db):
    topic_id = topics.create_topic(db, 1, "Long running")
    db.execute(
        "UPDATE discussion_topics SET rolling_summary = ? WHERE id = ?",
        ("\n".join(f"line {i} " + "x" * 60 for i in range(topics.SUMMARY_MAX_LINES)), topic_id),
    )
    db.commit()
    session_id = _session(db, texts=("newest",), topic_id=topic_id)
    line = topics.append_session_summary_to_topic(db, app.config, session_id)
    topic = topics.get_topic(db, topic_id)
    assert len(topic.rolling_summary) <= topics.SUMMARY_MAX_CHARS
    assert len(topic.summary_lines) <= topics.SUMMARY_MAX_LINES
    assert topic.summary_lines[-1] == line
    assert "condensed away" in topic.summary_lines[0]


def test_recompress_uses_agent_reply_when_configured(app, db, monkeypatch):
    app.config["PLANNING_AGENT"] = "codex"
    monkeypatch.setattr(topics, "_ask_agent", lambda *a, **k: "- decided A\n- B is open\n")
    assert topics.recompress_summary(db, app.config, 1, "a\nb\nc") == "decided A\nB is open"
    # An unusable reply falls back to trimming.
    monkeypatch.setattr(topics, "_ask_agent", lambda *a, **k: "")
    assert topics.recompress_summary(db, app.config, 1, "a\nb") == "a\nb"


def test_summary_failure_does_not_block_archive(client, app, db, monkeypatch):
    topic_id = topics.create_topic(db, 1, "Fragile")
    session_id = _session(db, topic_id=topic_id)

    def boom(*a, **k):
        raise RuntimeError("summarizer down")

    monkeypatch.setattr(topics, "append_session_summary_to_topic", boom)
    resp = client.post(f"/sessions/{session_id}/archive", headers=AJAX)
    assert resp.status_code == 200
    assert get_agent_session(db, session_id).metadata.get("archived_at")


# -- routes (tasks 86-88, 91-94) ------------------------------------------------------


def _project_with_repo(client, app, name="Topic Project"):
    repo_path = os.path.join(app.config["allowed_root"], name.replace(" ", "-"))
    os.makedirs(repo_path, exist_ok=True)
    client.post("/projects/new", data={"name": name, "description": "", "repo_name": "main", "repo_path": repo_path})
    with app.app_context():
        return get_db().execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()[0]


def _latest_session(app):
    with app.app_context():
        return get_agent_session(get_db(), get_db().execute("SELECT MAX(id) FROM agent_sessions").fetchone()[0])


def _first_prompt(app, session_id):
    with app.app_context():
        return next(e.data for e in list_agent_events(get_db(), session_id) if e.event_type == "PromptSubmitted")


def test_start_form_lists_topics_and_preselects(client, app):
    project_id = _project_with_repo(client, app)
    with app.app_context():
        topic_id = topics.create_topic(get_db(), project_id, "Auth rework")
    html = client.get(f"/sessions/project/{project_id}?topic={topic_id}").get_data(as_text=True)
    assert 'name="topic"' in html and "New topic" in html
    assert f'<option value="{topic_id}" selected>Auth rework</option>' in html
    assert 'id="warmStartBox" checked' not in html


def test_start_session_with_new_topic_records_it(client, app):
    project_id = _project_with_repo(client, app)
    client.post(
        f"/sessions/project/{project_id}/create",
        data={"agent_type": "fake", "topic": "new", "new_topic_name": "Pipeline retries"},
    )
    session = _latest_session(app)
    assert session.topic_id is not None
    injected = session.metadata["injected_context"]
    assert injected["topic_id"] == session.topic_id and injected["topic_name"] == "Pipeline retries"


def test_start_session_in_topic_puts_topic_context_first(client, app):
    project_id = _project_with_repo(client, app)
    with app.app_context():
        db = get_db()
        topic_id = topics.create_topic(db, project_id, "Auth rework")
        db.execute("UPDATE discussion_topics SET rolling_summary = 'picked JWT' WHERE id = ?", (topic_id,))
        db.commit()
    client.post(
        f"/sessions/project/{project_id}/create",
        data={"agent_type": "fake", "topic": str(topic_id), "warm_start": "1"},
    )
    session = _latest_session(app)
    prompt = _first_prompt(app, session.id)
    assert prompt.startswith("[System Context]\nThis session continues the topic \"Auth rework\"")
    assert "picked JWT" in prompt
    assert session.metadata["injected_context"]["topic_context"] is True


def test_start_session_without_topic_unchanged(client, app):
    project_id = _project_with_repo(client, app)
    client.post(f"/sessions/project/{project_id}/create", data={"agent_type": "fake"})
    session = _latest_session(app)
    assert session.topic_id is None
    assert session.metadata["injected_context"]["topic_id"] is None


def test_start_session_rejects_bad_topic(client, app):
    project_id = _project_with_repo(client, app)
    resp = client.post(
        f"/sessions/project/{project_id}/create",
        data={"agent_type": "fake", "topic": "new", "new_topic_name": ""}, follow_redirects=True,
    )
    assert "A topic name is required." in resp.get_data(as_text=True)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0] == 0


def test_add_to_topic_action(client, app):
    project_id = _project_with_repo(client, app)
    client.post(f"/sessions/project/{project_id}/create", data={"agent_type": "fake"})
    session = _latest_session(app)
    page = client.get(f"/sessions/{session.id}").get_data(as_text=True)
    assert "Add to Topic" in page

    resp = client.post(
        f"/sessions/{session.id}/topic", data={"topic": "new", "new_topic_name": "Mid-chat"}, headers=AJAX,
    )
    assert resp.status_code == 200 and resp.json["topic_name"] == "Mid-chat"
    assert _latest_session(app).topic_id == resp.json["topic_id"]
    page = client.get(f"/sessions/{session.id}").get_data(as_text=True)
    assert 'class="topic-chip"' in page and "Change topic" in page

    resp = client.post(f"/sessions/{session.id}/topic", data={"topic": ""}, headers=AJAX)
    assert resp.status_code == 200 and _latest_session(app).topic_id is None

    other = _project_with_repo(client, app, "Other Project")
    with app.app_context():
        foreign = topics.create_topic(get_db(), other, "Not yours")
    resp = client.post(f"/sessions/{session.id}/topic", data={"topic": str(foreign)}, headers=AJAX)
    assert resp.status_code == 400
    assert client.post("/sessions/9999/topic", data={"topic": ""}, headers=AJAX).status_code == 404


def test_archiving_topic_session_updates_summary(client, app):
    app.config["PLANNING_AGENT"] = "fake"
    project_id = _project_with_repo(client, app)
    client.post(
        f"/sessions/project/{project_id}/create",
        data={"agent_type": "fake", "topic": "new", "new_topic_name": "Archived thread"},
    )
    session = _latest_session(app)
    client.post(f"/sessions/{session.id}/send", data={"prompt": "let's discuss retries"})
    client.post(f"/sessions/{session.id}/stop", headers=AJAX)
    assert client.post(f"/sessions/{session.id}/archive", headers=AJAX).status_code == 200
    with app.app_context():
        topic = topics.get_topic(get_db(), session.topic_id)
    assert len(topic.summary_lines) == 1


def test_session_badges_on_lists(client, app):
    project_id = _project_with_repo(client, app)
    client.post(
        f"/sessions/project/{project_id}/create",
        data={"agent_type": "fake", "topic": "new", "new_topic_name": "Badge topic"},
    )
    for url in (f"/sessions/project/{project_id}", "/sessions"):
        html = client.get(url).get_data(as_text=True)
        assert 'class="topic-chip"' in html and "Badge topic" in html


def test_topics_pages_and_actions(client, app):
    project_id = _project_with_repo(client, app)
    html = client.get(f"/projects/{project_id}/topics/").get_data(as_text=True)
    assert "No topics yet" in html
    assert 'aria-current="page">Topics</a>' in html  # project tab active

    resp = client.post(f"/projects/{project_id}/topics/", data={"name": "Auth rework"}, headers=AJAX)
    assert resp.status_code == 200
    topic_id = resp.json["topic_id"]
    assert client.post(f"/projects/{project_id}/topics/", data={"name": "auth rework"}, headers=AJAX).status_code == 400

    client.post(
        f"/sessions/project/{project_id}/create", data={"agent_type": "fake", "topic": str(topic_id)},
    )
    html = client.get(f"/projects/{project_id}/topics/").get_data(as_text=True)
    assert "Auth rework" in html and 'class="table-compact"' in html and "row-actions" in html
    detail = client.get(f"/projects/{project_id}/topics/{topic_id}").get_data(as_text=True)
    assert "Running summary" in detail and "Start session in this topic" in detail
    assert f"topic={topic_id}" in detail

    resp = client.post(f"/projects/{project_id}/topics/{topic_id}/rename", data={"name": "Auth v2"}, headers=AJAX)
    assert resp.json["name"] == "Auth v2"
    resp = client.post(f"/projects/{project_id}/topics/{topic_id}/archive", headers=AJAX)
    assert resp.json["status"] == "archived"
    assert "Auth v2" not in client.get(f"/projects/{project_id}/topics/").get_data(as_text=True)
    assert "Auth v2" in client.get(f"/projects/{project_id}/topics/?archived=1").get_data(as_text=True)
    # Archived topics stay reachable and keep their sessions.
    assert client.get(f"/projects/{project_id}/topics/{topic_id}").status_code == 200

    resp = client.patch(f"/projects/{project_id}/topics/{topic_id}", json={"status": "active", "name": "Auth v3"})
    assert resp.json == {"status": "updated", "name": "Auth v3", "topic_status": "active"}

    other = _project_with_repo(client, app, "Other Project")
    assert client.get(f"/projects/{other}/topics/{topic_id}").status_code == 404
    assert client.post(f"/projects/{other}/topics/{topic_id}/archive", headers=AJAX).status_code == 404
    assert client.get("/projects/9999/topics/").status_code == 404


def test_rename_non_ajax_fallback_redirects(client, app):
    project_id = _project_with_repo(client, app)
    with app.app_context():
        topic_id = topics.create_topic(get_db(), project_id, "Plain")
    resp = client.post(f"/projects/{project_id}/topics/{topic_id}/rename", data={"name": "Plainer"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/topics/{topic_id}")
