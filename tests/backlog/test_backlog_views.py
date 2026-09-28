import io

import pytest

from app.agents import models as agent_models
from app.artifacts import models as artifact_models
from app.backlog import persistence
from app.db import get_db

AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def project_id(client):
    resp = client.post("/projects/new", data={"name": "Proj", "description": ""})
    return int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])


def _base(pid):
    return f"/projects/{pid}/backlog"


def _add(client, pid, **data):
    resp = client.post(f"{_base(pid)}/items", data=data, headers=AJAX)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["item_id"]


def _item(app, item_id):
    with app.app_context():
        return persistence.get_item(get_db(), item_id)


def test_inbox_renders_items_and_counts(client, project_id):
    _add(client, project_id, text="Login is slow")
    html = client.get(f"{_base(project_id)}/inbox").get_data(as_text=True)
    assert "Login is slow" in html
    assert "Backlog" in html


def test_quick_add_requires_text_or_file(client, project_id):
    resp = client.post(f"{_base(project_id)}/items", data={"text": " "}, headers=AJAX)
    assert resp.status_code == 400 and "error" in resp.get_json()


def test_quick_add_non_ajax_redirects(client, project_id):
    resp = client.post(f"{_base(project_id)}/items", data={"text": "x"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/backlog/inbox")


def test_attachment_only_and_download(client, app, project_id):
    resp = client.post(
        f"{_base(project_id)}/items",
        data={"files": (io.BytesIO(b"\x89PNG data"), "shot.png")},
        headers=AJAX,
        content_type="multipart/form-data",
    )
    item_id = resp.get_json()["item_id"]
    with app.app_context():
        files = persistence.list_attachments(get_db(), item_id)
    assert [(f.kind, f.name) for f in files] == [("IMAGE", "shot.png")]
    dl = client.get(f"{_base(project_id)}/items/{item_id}/attachments/{files[0].id}")
    assert dl.status_code == 200 and dl.data == b"\x89PNG data"


def test_upload_filename_traversal_is_neutralised(client, app, project_id):
    resp = client.post(
        f"{_base(project_id)}/items",
        data={"files": (io.BytesIO(b"x"), "../../evil.txt")},
        headers=AJAX,
        content_type="multipart/form-data",
    )
    with app.app_context():
        files = persistence.list_attachments(get_db(), resp.get_json()["item_id"])
    assert ".." not in files[0].path


def test_triage_transition_and_priority(client, app, project_id):
    item_id = _add(client, project_id, text="a")
    url = f"{_base(project_id)}/items/{item_id}"
    assert client.post(url, data={"status": "TRIAGED", "priority": "HIGH"}, headers=AJAX).status_code == 200
    item = _item(app, item_id)
    assert (item.status, item.priority) == ("TRIAGED", "HIGH")
    # Query-string form used by the generic data-ajax-action buttons.
    assert client.post(f"{url}?status=SELECTED", headers=AJAX).status_code == 200
    assert _item(app, item_id).status == "SELECTED"


def test_invalid_transition_is_409(client, project_id):
    item_id = _add(client, project_id, text="a")
    resp = client.post(f"{_base(project_id)}/items/{item_id}", data={"status": "READY"}, headers=AJAX)
    assert resp.status_code == 409


def test_archive_via_delete(client, app, project_id):
    item_id = _add(client, project_id, text="a")
    resp = client.delete(f"{_base(project_id)}/items/{item_id}", headers=AJAX)
    assert resp.status_code == 200
    assert _item(app, item_id).status == "ARCHIVED"
    # Archiving twice is idempotent.
    assert client.delete(f"{_base(project_id)}/items/{item_id}", headers=AJAX).status_code == 200


def test_bulk_moves_valid_items_and_reports_failures(client, app, project_id):
    a, b = _add(client, project_id, text="a"), _add(client, project_id, text="b")
    client.delete(f"{_base(project_id)}/items/{b}", headers=AJAX)  # ARCHIVED -> cannot be TRIAGED
    resp = client.post(f"{_base(project_id)}/bulk", data={"status": "TRIAGED", "ids": [a, b]}, headers=AJAX)
    assert resp.status_code == 200
    assert resp.get_json()["changed"] == [a] and resp.get_json()["failed"] == [b]
    assert _item(app, a).status == "TRIAGED"
    assert client.post(f"{_base(project_id)}/bulk", data={"status": "TRIAGED"}, headers=AJAX).status_code == 400


def test_items_are_scoped_to_their_project(client, project_id):
    item_id = _add(client, project_id, text="a")
    other = int(client.post("/projects/new", data={"name": "Other"}).headers["Location"].rsplit("/", 1)[-1])
    assert client.get(f"{_base(other)}/items/{item_id}").status_code == 404
    assert client.post(f"{_base(other)}/items/{item_id}", data={"status": "TRIAGED"}, headers=AJAX).status_code == 404
    assert client.get("/projects/999/backlog/inbox").status_code == 404


def test_triage_and_sprint_pages(client, project_id):
    a = _add(client, project_id, text="pending triage")
    b = _add(client, project_id, text="chosen")
    client.post(f"{_base(project_id)}/items/{b}?status=SELECTED", headers=AJAX)
    assert "pending triage" in client.get(f"{_base(project_id)}/triage").get_data(as_text=True)
    sprint = client.get(f"{_base(project_id)}/sprint").get_data(as_text=True)
    assert "chosen" in sprint and "pending triage" not in sprint
    assert client.get(f"{_base(project_id)}/items/{a}").status_code == 200


# -- research action (§50, task 44) ------------------------------------------


@pytest.fixture
def fake_researcher(app):
    app.config["PLANNING_AGENT"] = "fake"


def _research_url(pid, item_id):
    return f"{_base(pid)}/items/{item_id}/research"


def test_research_action_completes_and_creates_artifact_and_link(client, app, project_id, fake_researcher):
    item_id = _add(client, project_id, text="Investigate slow login", title="Slow login")
    resp = client.post(_research_url(project_id, item_id), headers=AJAX)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["outcome"] == "COMPLETE"
    assert body["report"]["summary"]
    assert len(body["report"]["findings"]) == 2
    assert body["link_id"] and body["research_session_id"]

    with app.app_context():
        db = get_db()
        links = persistence.list_research_links(db, item_id)
        assert len(links) == 1
        link = links[0]
        assert link.status == "PENDING"
        assert link.outcome_state == "COMPLETE"
        assert link.artifact_id is not None

        session_row = agent_models.get_research_session(db, link.research_session_id)
        assert session_row.status == "COMPLETED"
        assert session_row.project_id == project_id

        artifact = artifact_models.get_artifact(db, link.artifact_id)
        assert artifact.kind == "research_report"
        assert artifact.metadata["backlog_item_id"] == item_id
        assert f"backlog-item-{item_id}" in artifact.tags

        # Research never changes the item's status.
        assert persistence.get_item(db, item_id).status == "INBOX"


def test_research_history_renders_on_item_page(client, project_id, fake_researcher):
    item_id = _add(client, project_id, text="Investigate slow login")
    client.post(_research_url(project_id, item_id), headers=AJAX)
    html = client.get(f"{_base(project_id)}/items/{item_id}").get_data(as_text=True)
    assert "Research findings" in html
    assert "unverified" in html


def test_accept_research_persists_summary_and_marks_accepted(client, app, project_id, fake_researcher):
    item_id = _add(client, project_id, text="Investigate slow login")
    resp = client.post(_research_url(project_id, item_id), headers=AJAX)
    link_id = resp.get_json()["link_id"]

    accept = client.post(f"{_base(project_id)}/items/{item_id}/research/{link_id}/accept", headers=AJAX)
    assert accept.status_code == 200, accept.get_json()
    assert "Automated research summary" in accept.get_json()["text"]

    with app.app_context():
        db = get_db()
        item = persistence.get_item(db, item_id)
        assert "Investigate slow login" in item.text
        assert "Automated research summary" in item.text
        assert item.status == "INBOX"  # accepting research never changes status

        link = persistence.get_research_link(db, link_id)
        assert link.status == "ACCEPTED"

        history = persistence.list_history(db, item_id)
        assert any("research summary" in h.notes.lower() for h in history)

    # A reviewed link cannot be accepted again.
    again = client.post(f"{_base(project_id)}/items/{item_id}/research/{link_id}/accept", headers=AJAX)
    assert again.status_code == 409


def test_dismiss_research_does_not_persist(client, app, project_id, fake_researcher):
    item_id = _add(client, project_id, text="Investigate slow login")
    resp = client.post(_research_url(project_id, item_id), headers=AJAX)
    link_id = resp.get_json()["link_id"]

    dismiss = client.post(f"{_base(project_id)}/items/{item_id}/research/{link_id}/dismiss", headers=AJAX)
    assert dismiss.status_code == 200

    with app.app_context():
        db = get_db()
        item = persistence.get_item(db, item_id)
        assert item.text == "Investigate slow login"
        assert persistence.get_research_link(db, link_id).status == "DISMISSED"

    again = client.post(f"{_base(project_id)}/items/{item_id}/research/{link_id}/dismiss", headers=AJAX)
    assert again.status_code == 409


def test_research_scoped_to_project(client, project_id, fake_researcher):
    item_id = _add(client, project_id, text="a")
    other = int(client.post("/projects/new", data={"name": "Other"}).headers["Location"].rsplit("/", 1)[-1])
    assert client.post(_research_url(other, item_id), headers=AJAX).status_code == 404
