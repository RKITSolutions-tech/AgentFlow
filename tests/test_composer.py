import io
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.agents import models
from app.agents.codex import CodexAdapter
from app.db import get_db
from app.projects import models as project_models
from app.sessions import composer

AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def repo_dir(app):
    path = os.path.join(app.config["allowed_root"], "repo")
    os.makedirs(os.path.join(path, "src"))
    os.makedirs(os.path.join(path, ".git"))
    os.makedirs(os.path.join(path, "node_modules"))
    for rel in ("README.md", "src/app.py", "src/util.py", ".git/config", "node_modules/x.js"):
        open(os.path.join(path, rel), "w").close()
    return path


def _session(app, repo_dir, agent="fake", status="COMPLETED", **metadata):
    with app.app_context():
        db = get_db()
        pid = project_models.create_project(db, f"P-{agent}-{status}")
        sid = models.create_agent_session(
            db, pid, agent, metadata={"working_directory": repo_dir, **metadata}
        )
        models.set_session_status(db, sid, status)
        return sid


def _events(app, sid):
    with app.app_context():
        return [(e.event_type, e.data) for e in models.list_agent_events(get_db(), sid)]


# -- slash commands ----------------------------------------------------------


def test_parse_command():
    assert composer.parse_command("/Rename  New title ") == ("rename", "New title")
    assert composer.parse_command("/help") == ("help", "")
    assert composer.parse_command("//not a command") is None
    assert composer.parse_command("plain") is None


def test_composer_config_gates_by_capability(client, app, repo_dir):
    fake = client.get(f"/sessions/{_session(app, repo_dir)}/composer").get_json()
    names = {c["name"] for c in fake["commands"]}
    assert {"help", "rename", "archive", "stop", "fork"} <= names
    assert not {"model", "mode", "usage"} & names
    assert fake["permission_modes"] == []
    codex = client.get(f"/sessions/{_session(app, repo_dir, agent='codex')}/composer").get_json()
    names = {c["name"] for c in codex["commands"]}
    assert {"model", "mode", "usage"} <= names and "fork" not in names
    assert codex["permission_modes"] == ["read-only", "workspace-write"]
    assert codex["images"] is True


def _cmd(client, sid, text):
    return client.post(f"/sessions/{sid}/command", data={"command": text})


def test_command_rename_help_unknown(client, app, repo_dir):
    sid = _session(app, repo_dir)
    assert _cmd(client, sid, "/rename Nice name").get_json()["title"] == "Nice name"
    assert "/archive" in _cmd(client, sid, "/help").get_json()["message"]
    bad = _cmd(client, sid, "/nope")
    assert bad.status_code == 400 and "Unknown command" in bad.get_json()["error"]
    assert _cmd(client, sid, "/model x").status_code == 400  # fake lacks model_selection
    assert _cmd(client, sid, "plain").status_code == 400


def test_command_mode_model_usage_on_codex(client, app, repo_dir):
    sid = _session(app, repo_dir, agent="codex", usage={"input_tokens": 4, "output_tokens": 1})
    assert _cmd(client, sid, "/mode read-only").status_code == 200
    assert _cmd(client, sid, "/mode danger-full-access").status_code == 400
    assert _cmd(client, sid, "/model gpt-x").status_code == 200
    with app.app_context():
        meta = models.get_agent_session(get_db(), sid).metadata
    assert meta["permission_mode"] == "read-only" and meta["model"] == "gpt-x"
    assert "input tokens: 4" in _cmd(client, sid, "/usage").get_json()["message"]
    _cmd(client, sid, "/model default")
    with app.app_context():
        assert "model" not in models.get_agent_session(get_db(), sid).metadata


def test_command_fork_archive_stop(client, app, repo_dir):
    sid = _session(app, repo_dir)
    assert "/sessions/" in _cmd(client, sid, "/fork").get_json()["redirect"]
    assert _cmd(client, sid, "/archive").get_json()["redirect"].startswith("/sessions/project/")
    running = _session(app, repo_dir, status="RUNNING")
    assert _cmd(client, running, "/archive").status_code == 400
    assert _cmd(client, running, "/stop").get_json()["reload"] is True
    with app.app_context():
        assert models.get_agent_session(get_db(), running).status == "STOPPED"


# -- mentions ----------------------------------------------------------------


def test_mentions_rank_names_and_skip_vendor_dirs(client, app, repo_dir):
    sid = _session(app, repo_dir)
    paths = client.get(f"/sessions/{sid}/mentions?q=app").get_json()["paths"]
    assert paths == [os.path.join("src", "app.py")]
    everything = client.get(f"/sessions/{sid}/mentions?q=").get_json()["paths"]
    assert "README.md" in everything
    assert not any(p.startswith((".git", "node_modules")) for p in everything)
    assert client.get(f"/sessions/{sid}/mentions?q=src").get_json()["paths"] == [
        os.path.join("src", "app.py"), os.path.join("src", "util.py")]


def test_mentions_refuse_directory_outside_allowed_roots(client, app, tmp_path):
    sid = _session(app, str(tmp_path))
    assert client.get(f"/sessions/{sid}/mentions?q=").get_json()["paths"] == []


# -- attachments -------------------------------------------------------------


def _upload(client, sid, name, data):
    return client.post(
        f"/sessions/{sid}/attachments",
        data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data",
    )


def test_attachment_upload_and_send(client, app, repo_dir):
    sid = _session(app, repo_dir)
    doc = _upload(client, sid, "../notes.txt", b"hello").get_json()
    img = _upload(client, sid, "shot.PNG", b"\x89PNG").get_json()
    assert doc["name"] == "notes.txt" and not doc["is_image"] and img["is_image"]
    resp = client.post(f"/sessions/{sid}/send",
                       data={"prompt": "look", "attachment": [doc["id"], img["id"]]})
    assert resp.status_code == 200
    prompt = _events(app, sid)[-1][1]
    assert prompt.startswith("look\n\nAttached files:\n- ")
    assert doc["id"] in prompt and img["id"] not in prompt  # images go natively, not by path
    assert os.path.dirname(os.path.dirname(prompt.split("- ")[1])).endswith("attachments")


def test_attachment_validation(client, app, repo_dir, monkeypatch):
    sid = _session(app, repo_dir)
    other = _session(app, repo_dir)
    owned = _upload(client, other, "a.txt", b"x").get_json()
    for bad in ("../x", "nonexistent", owned["id"]):
        r = client.post(f"/sessions/{sid}/send", data={"prompt": "p", "attachment": bad})
        assert r.status_code == 400 and "Unknown attachment" in r.get_json()["error"]
    monkeypatch.setattr(composer, "MAX_ATTACHMENT_SIZE", 4)
    assert _upload(client, sid, "big.txt", b"12345").status_code == 413
    assert client.post(f"/sessions/{sid}/attachments").status_code == 400
    assert client.post(f"/sessions/{sid}/send", data={"prompt": "p", "attachment": ["x"] * 6}).status_code == 400


def test_codex_flags():
    assert CodexAdapter._permission_flags("read-only") == ["-c", 'sandbox_mode="read-only"']
    assert CodexAdapter._permission_flags("danger-full-access") == []
    assert CodexAdapter._permission_flags(None) == []
    assert CodexAdapter._image_flags(["/a.png", "/b.png"]) == ["--image=/a.png", "--image=/b.png"]
    assert CodexAdapter._image_flags(None) == []


# -- scheduled messages ------------------------------------------------------


def _future(minutes=5):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def test_schedule_validation(client, app, repo_dir):
    sid = _session(app, repo_dir)
    url = f"/sessions/{sid}/schedule"
    assert client.post(url, data={"content": "hi", "send_at": "garbage"}).status_code == 400
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert client.post(url, data={"content": "hi", "send_at": past}).status_code == 400
    assert client.post(url, data={"content": " ", "send_at": _future()}).status_code == 400
    ok = client.post(url, data={"content": "later", "send_at": _future()})
    assert ok.status_code == 201 and len(ok.get_json()["scheduled"]) == 1


def test_cancel_scheduled(client, app, repo_dir):
    sid = _session(app, repo_dir)
    mid = client.post(f"/sessions/{sid}/schedule",
                      data={"content": "x", "send_at": _future()}).get_json()["id"]
    assert client.post(f"/sessions/{sid}/schedule/{mid}/cancel").get_json()["scheduled"] == []
    assert client.post(f"/sessions/{sid}/schedule/{mid}/cancel").status_code == 404


def _make_due(app, sid, content="due now"):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO scheduled_messages (session_id, content, send_at) "
            "VALUES (?, ?, datetime('now', '-1 minute'))", (sid, content))
        db.commit()


def test_due_message_sent_when_session_polled(client, app, repo_dir):
    sid = _session(app, repo_dir)
    _make_due(app, sid)
    client.get(f"/sessions/{sid}/stream")
    assert ("PromptSubmitted", "due now") in _events(app, sid)
    with app.app_context():
        assert composer.list_scheduled(get_db(), sid, pending_only=False)[0]["status"] == "SENT"
    client.get(f"/sessions/{sid}/stream")
    assert _events(app, sid).count(("PromptSubmitted", "due now")) == 1


def test_due_message_waits_for_running_turn_and_future_untouched(client, app, repo_dir):
    busy = _session(app, repo_dir, status="RUNNING")
    _make_due(app, busy)
    with app.app_context():
        db = get_db()
        assert composer.dispatch_due(db, lambda s: pytest.fail("must not send")) == 0
        assert composer.list_scheduled(db, busy)[0]["status"] == "PENDING"
    idle = _session(app, repo_dir)
    client.post(f"/sessions/{idle}/schedule", data={"content": "x", "send_at": _future()})
    client.get(f"/sessions/{idle}/stream")
    assert _events(app, idle) == []


def test_failed_delivery_recorded(client, app, repo_dir):
    sid = _session(app, repo_dir, status="STOPPED")
    _make_due(app, sid)

    class Boom:
        def send(self, *_a, **_k):
            raise ValueError("session has been stopped")

    with app.app_context():
        db = get_db()
        composer.dispatch_due(db, lambda _s: Boom())
        row = composer.list_scheduled(db, sid, pending_only=False)[0]
    assert row["status"] == "FAILED" and "stopped" in row["error"]


def test_dispatch_cli(app, repo_dir):
    sid = _session(app, repo_dir)
    _make_due(app, sid)
    result = app.test_cli_runner().invoke(args=["sessions", "dispatch-scheduled"])
    assert "Sent 1" in result.output
    assert ("PromptSubmitted", "due now") in _events(app, sid)


def test_chat_page_has_composer(client, app, repo_dir):
    page = client.get(f"/sessions/{_session(app, repo_dir)}").data
    for needle in (b'id="composerMenu"', b'id="attachBtn"', b'id="scheduleBtn"', b'id="promptInput"'):
        assert needle in page
