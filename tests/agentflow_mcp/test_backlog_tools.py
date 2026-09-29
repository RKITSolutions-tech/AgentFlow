import pytest

from app.backlog.models import InvalidTransitionError
from app.db import get_db
from app.mcp.tools import backlog as backlog_tools


@pytest.fixture
def db(app):
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        conn.execute("INSERT INTO projects (name, slug) VALUES ('Other', 'other')")
        conn.commit()
        yield conn


def test_create_list_get_roundtrip(db):
    created = backlog_tools.create_item(db, 1, title="Fix login", text="details", priority="high", created_by="agent:1")
    assert created["status"] == "INBOX"
    assert created["priority"] == "HIGH"
    assert created["created_by"] == "agent:1"

    listed = backlog_tools.list_items(db, 1)
    assert [item["id"] for item in listed] == [created["id"]]

    fetched = backlog_tools.get_item(db, 1, created["id"])
    assert fetched == created


def test_update_item_content_fields(db):
    created = backlog_tools.create_item(db, 1, text="a")
    updated = backlog_tools.update_item(db, 1, created["id"], title="New title", priority="low")
    assert (updated["title"], updated["priority"]) == ("New title", "LOW")


def test_transition_enforces_state_machine(db):
    created = backlog_tools.create_item(db, 1, text="a")
    moved = backlog_tools.transition_item(db, 1, created["id"], "TRIAGED", notes="looks real", changed_by="agent:1")
    assert moved["status"] == "TRIAGED"
    with pytest.raises(InvalidTransitionError):
        backlog_tools.transition_item(db, 1, created["id"], "RELEASED")


def test_record_note_preserves_status(db):
    created = backlog_tools.create_item(db, 1, text="a")
    result = backlog_tools.record_note(db, 1, created["id"], "just a note", changed_by="agent:1")
    assert result["status"] == "INBOX"
    history = backlog_tools.list_history(db, 1, created["id"])
    assert history[-1]["notes"] == "just a note"
    assert history[-1]["old_status"] == history[-1]["new_status"] == "INBOX"


def test_tools_cannot_cross_project_boundary(db):
    created = backlog_tools.create_item(db, 1, text="a")
    with pytest.raises(LookupError):
        backlog_tools.get_item(db, 2, created["id"])
    with pytest.raises(LookupError):
        backlog_tools.transition_item(db, 2, created["id"], "TRIAGED")
    with pytest.raises(LookupError):
        backlog_tools.update_item(db, 2, created["id"], title="hijacked")
