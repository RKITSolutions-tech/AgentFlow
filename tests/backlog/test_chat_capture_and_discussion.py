"""Backlog <-> chat (tasks 57/58): capturing a Backlog item from chat messages,
and item-scoped discussions whose agreed replies update the item."""
from app.agents.models import get_agent_session, list_agent_events
from app.backlog import discussion, persistence
from app.db import get_db
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}


def _session(client, app, script):
    """A fake-agent GENERAL session that has played `script`."""
    from app.sessions import starter

    pid, repo = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        session_id = starter.start_interactive_session(app.config, get_db(), pid, repo, "fake", script=script)
    return pid, session_id


def _events(app, session_id, event_type):
    with app.app_context():
        return [e for e in list_agent_events(get_db(), session_id) if e.event_type == event_type]


# -- task 57: capture from chat -------------------------------------------------------


def test_capture_selected_messages_creates_inbox_item(client, app):
    pid, sid = _session(client, app, [{"action": "message", "text": "We should add retry with backoff.\nDetails follow."}])
    prompt = _events(app, sid, "PromptSubmitted")[0]
    reply = _events(app, sid, "AgentText")[0]
    resp = client.post(f"/sessions/{sid}/backlog-item", data={"event_id": [str(reply.id), str(prompt.id)]}, headers=AJAX)
    assert resp.status_code == 201
    body = resp.json
    assert body["status"] == "success" and body["url"].endswith(f"/backlog/items/{body['backlog_item_id']}")
    with app.app_context():
        db = get_db()
        item = persistence.get_item(db, body["backlog_item_id"])
        assert item.project_id == pid and item.status == "INBOX"
        assert (item.source_type, item.source_reference) == ("chat_session", str(sid))
        assert item.title.startswith("Developer") is False  # title from the first selected message's first line
        assert item.text.index("Developer:") < item.text.index("Agent: We should add retry")  # chronological
        links = persistence.list_attachments(db, item.id)
        assert links[0].kind == "LINK" and links[0].path == f"/sessions/{sid}"
        assert "captured from chat session" in persistence.list_history(db, item.id)[-1].notes
    page = client.get(f"/projects/{pid}/backlog/items/{body['backlog_item_id']}").get_data(as_text=True)
    assert f'href="/sessions/{sid}">chat session #{sid}</a>' in page


def test_capture_uses_given_title_and_description(client, app):
    pid, sid = _session(client, app, [{"action": "message", "text": "x"}])
    reply = _events(app, sid, "AgentText")[0]
    resp = client.post(
        f"/sessions/{sid}/backlog-item",
        data={"event_id": str(reply.id), "title": "Retry logic", "description": "Edited <b>text</b>", "priority": "HIGH"},
        headers=AJAX,
    )
    with app.app_context():
        item = persistence.get_item(get_db(), resp.json["backlog_item_id"])
    assert (item.title, item.text, item.priority) == ("Retry logic", "Edited <b>text</b>", "HIGH")
    # Stored verbatim; the item page escapes it.
    page = client.get(f"/projects/{pid}/backlog/items/{item.id}").get_data(as_text=True)
    assert "Edited <b>text</b>" not in page and "Edited &lt;b&gt;text&lt;/b&gt;" in page


def test_capture_validation(client, app):
    pid, sid = _session(client, app, [{"action": "message", "text": "x"}])
    _, other = _session(client, app, [{"action": "message", "text": "y"}])
    foreign = _events(app, other, "AgentText")[0]
    assert client.post(f"/sessions/{sid}/backlog-item", data={"event_id": str(foreign.id)}, headers=AJAX).status_code == 404
    assert client.post(f"/sessions/{sid}/backlog-item", data={"event_id": "abc"}, headers=AJAX).status_code == 400
    resp = client.post(f"/sessions/{sid}/backlog-item", data={}, headers=AJAX)
    assert resp.status_code == 400 and resp.json["status"] == "error"
    assert client.post("/sessions/9999/backlog-item", data={}, headers=AJAX).status_code == 404


def test_capture_truncates_long_selections(client, app):
    pid, sid = _session(client, app, [{"action": "message", "text": "z" * 20000}])
    reply = _events(app, sid, "AgentText")[0]
    resp = client.post(f"/sessions/{sid}/backlog-item", data={"event_id": str(reply.id)}, headers=AJAX)
    with app.app_context():
        item = persistence.get_item(get_db(), resp.json["backlog_item_id"])
    assert len(item.text) < 8100 and item.text.endswith("[...truncated]")


def test_capture_non_ajax_redirects_back(client, app):
    pid, sid = _session(client, app, [{"action": "message", "text": "x"}])
    reply = _events(app, sid, "AgentText")[0]
    resp = client.post(f"/sessions/{sid}/backlog-item", data={"event_id": str(reply.id)})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/sessions/{sid}")


def test_chat_page_has_capture_controls(client, app):
    pid, sid = _session(client, app, [])
    html = client.get(f"/sessions/{sid}").get_data(as_text=True)
    assert 'id="captureBar"' in html and 'id="captureForm"' in html and "chat-capture-toggle" in html


# -- task 58: item-scoped discussion --------------------------------------------------


def _item(app, pid, **kw):
    with app.app_context():
        db = get_db()
        item_id = persistence.create_item(db, pid, text=kw.get("text", "Retry failing steps"), title="Retries")
        persistence.add_attachment(db, item_id, "LINK", "spec", "https://example.com/spec")
        persistence.record_note(db, item_id, "looked at it", "sam")
        return item_id


def test_item_context_has_text_attachments_history(client, app):
    pid, _ = create_project_with_repo(client, app.config["allowed_root"])
    item_id = _item(app, pid)
    with app.app_context():
        context = discussion.item_context(get_db(), persistence.get_item(get_db(), item_id))
    assert f"Backlog item #{item_id}" in context and "Retry failing steps" in context
    assert "link: spec (https://example.com/spec)" in context
    assert "looked at it" in context and "```backlog-item" in context


def test_discuss_starts_session_with_item_context(client, app):
    pid, _ = create_project_with_repo(client, app.config["allowed_root"])
    item_id = _item(app, pid)
    resp = client.post(f"/projects/{pid}/backlog/items/{item_id}/chat", data={"agent_type": "fake"}, headers=AJAX)
    assert resp.status_code == 200
    session_id = resp.json["session_id"]
    assert resp.json["redirect"] == f"/sessions/{session_id}"
    with app.app_context():
        db = get_db()
        session = get_agent_session(db, session_id)
        assert session.role == "GENERAL" and session.metadata["backlog_item_id"] == item_id
        assert session.metadata["injected_context"]["extra_context"] is True
        assert [s.id for s in discussion.discussions_for_item(db, item_id)] == [session_id]
    prompt = _events(app, session_id, "PromptSubmitted")[0].data
    assert "Retry failing steps" in prompt
    page = client.get(f"/projects/{pid}/backlog/items/{item_id}").get_data(as_text=True)
    assert "Discuss with agent" in page and f'href="/sessions/{session_id}"' in page
    chat = client.get(f"/sessions/{session_id}").get_data(as_text=True)
    assert f"Backlog #{item_id}" in chat and "Apply to item" in chat


def test_discuss_rejects_unknown_agent_and_missing_repo(client, app):
    pid, _ = create_project_with_repo(client, app.config["allowed_root"])
    item_id = _item(app, pid)
    assert client.post(f"/projects/{pid}/backlog/items/{item_id}/chat", data={"agent_type": "evil"}, headers=AJAX).status_code == 400
    client.post("/projects/new", data={"name": "No repo", "description": ""})
    with app.app_context():
        bare = get_db().execute("SELECT id FROM projects WHERE name = 'No repo'").fetchone()[0]
        bare_item = persistence.create_item(get_db(), bare, text="x")
    resp = client.post(f"/projects/{bare}/backlog/items/{bare_item}/chat", data={"agent_type": "fake"}, headers=AJAX)
    assert resp.status_code == 400


def test_apply_reply_updates_item_through_transition(client, app):
    pid, _ = create_project_with_repo(client, app.config["allowed_root"])
    item_id = _item(app, pid)
    resp = client.post(f"/projects/{pid}/backlog/items/{item_id}/chat", data={"agent_type": "fake"}, headers=AJAX)
    session_id = resp.json["session_id"]
    from app.agents.models import add_agent_event

    with app.app_context():
        reply = add_agent_event(get_db(), session_id, "AgentText",
                                "Here is a tighter version:\n```backlog-item\nRetry failed steps up to 3 times.\n```")
    prompt = _events(app, session_id, "PromptSubmitted")[0]
    url = f"/projects/{pid}/backlog/items/{item_id}/chat/{session_id}/apply"
    # Only agent replies of this discussion can be applied.
    assert client.post(url, data={"event_id": str(prompt.id)}, headers=AJAX).status_code == 400
    assert client.post(url, data={}, headers=AJAX).status_code == 400
    resp = client.post(url, data={"event_id": str(reply), "mode": "replace"}, headers=AJAX)
    assert resp.status_code == 200 and resp.json["item_status"] == "TRIAGED"
    with app.app_context():
        db = get_db()
        item = persistence.get_item(db, item_id)
        assert item.text == "Retry failed steps up to 3 times." and item.status == "TRIAGED"
        last = persistence.list_history(db, item_id)[-1]
        assert (last.old_status, last.new_status, last.changed_by) == ("INBOX", "TRIAGED", "agent_discussion")
    # Appending to an already-triaged item keeps its status and logs a note.
    resp = client.post(url, data={"event_id": str(reply), "mode": "append"}, headers=AJAX)
    with app.app_context():
        db = get_db()
        item = persistence.get_item(db, item_id)
        assert item.text.count("Retry failed steps") == 2 and item.status == "TRIAGED"
        assert "extended from chat session" in persistence.list_history(db, item_id)[-1].notes


def test_apply_requires_the_items_own_discussion(client, app):
    pid, repo = create_project_with_repo(client, app.config["allowed_root"])
    item_id = _item(app, pid)
    from app.sessions import starter

    with app.app_context():
        plain = starter.start_interactive_session(app.config, get_db(), pid, repo, "fake", script=[{"action": "message", "text": "hi"}])
    reply = _events(app, plain, "AgentText")[0]
    resp = client.post(f"/projects/{pid}/backlog/items/{item_id}/chat/{plain}/apply", data={"event_id": str(reply.id)}, headers=AJAX)
    assert resp.status_code == 404


def test_extract_proposal():
    assert discussion.extract_proposal("intro\n```backlog-item\nNew text\n```\nouter") == "New text"
    assert discussion.extract_proposal("  just text ") == "just text"


def test_upload_in_discussion_becomes_item_attachment(client, app):
    import io

    pid, _ = create_project_with_repo(client, app.config["allowed_root"])
    item_id = _item(app, pid)
    resp = client.post(f"/projects/{pid}/backlog/items/{item_id}/chat", data={"agent_type": "fake"}, headers=AJAX)
    session_id = resp.json["session_id"]
    upload = client.post(f"/sessions/{session_id}/attachments",
                         data={"file": (io.BytesIO(b"log line"), "trace.log")}, content_type="multipart/form-data")
    assert upload.status_code == 201 and upload.json["backlog_attachment_id"]
    with app.app_context():
        files = [a for a in persistence.list_attachments(get_db(), item_id) if a.kind != "LINK"]
    assert [f.name for f in files] == ["trace.log"]
    # A plain session's uploads stay session-only.
    _, plain = _session(client, app, [])
    upload = client.post(f"/sessions/{plain}/attachments",
                         data={"file": (io.BytesIO(b"x"), "a.txt")}, content_type="multipart/form-data")
    assert "backlog_attachment_id" not in upload.json
