import os

import pytest

from app.backlog import attachments, persistence
from app.backlog.models import InvalidTransitionError
from app.db import get_db
from app.runs.artifacts import ArtifactPathError


@pytest.fixture
def db(app):
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        conn.commit()
        yield conn


def test_create_and_get_item(db):
    item_id = persistence.create_item(db, 1, text=" fix login ", priority="high")
    item = persistence.get_item(db, item_id)
    assert (item.text, item.status, item.priority, item.title) == ("fix login", "INBOX", "HIGH", "")
    assert [h.new_status for h in persistence.list_history(db, item_id)] == ["INBOX"]


def test_invalid_priority_rejected(db):
    with pytest.raises(ValueError):
        persistence.create_item(db, 1, text="x", priority="urgent")


def test_update_and_filter(db):
    a = persistence.create_item(db, 1, text="a", priority="low")
    persistence.create_item(db, 1, text="b", priority="high")
    persistence.update_item(db, a, title="Title", sprint_id=7)
    assert persistence.get_item(db, a).title == "Title"
    assert [i.id for i in persistence.list_items(db, 1, sprint_id=7)] == [a]
    assert len(persistence.list_items(db, 1, priority="HIGH")) == 1
    with pytest.raises(ValueError):
        persistence.update_item(db, a, status="READY")


def test_transitions_recorded_and_enforced(db):
    item_id = persistence.create_item(db, 1, text="a")
    persistence.transition(db, item_id, "TRIAGED", notes="looks real", changed_by="me")
    assert persistence.get_item(db, item_id).status == "TRIAGED"
    with pytest.raises(InvalidTransitionError):
        persistence.transition(db, item_id, "RELEASED")
    last = persistence.list_history(db, item_id)[-1]
    assert (last.old_status, last.new_status, last.notes, last.changed_by) == (
        "INBOX", "TRIAGED", "looks real", "me",
    )


def test_attachment_storage_and_cascade(db, tmp_path):
    root = str(tmp_path / "art")
    item_id = persistence.create_item(db, 1, text="a")
    first = attachments.store_attachment(db, root, 1, item_id, "shot.png", b"png")
    second = attachments.store_attachment(db, root, 1, item_id, "shot.png", b"png2")
    listed = persistence.list_attachments(db, item_id)
    assert [a.kind for a in listed] == ["IMAGE", "IMAGE"]
    assert listed[0].path != listed[1].path  # never overwritten
    with open(attachments.attachment_file(root, persistence.get_attachment(db, item_id, first)), "rb") as fh:
        assert fh.read() == b"png"
    assert persistence.get_attachment(db, item_id, second).size == 4
    persistence.delete_item(db, item_id)
    assert persistence.list_attachments(db, item_id) == []


def test_path_traversal_filename_is_neutralised(db, tmp_path):
    root = str(tmp_path / "art")
    item_id = persistence.create_item(db, 1, text="a")
    att_id = attachments.store_attachment(db, root, 1, item_id, "../../../evil.txt", b"x")
    att = persistence.get_attachment(db, item_id, att_id)
    path = attachments.attachment_file(root, att)
    assert path.startswith(os.path.realpath(root) + os.sep)
    assert att.path.startswith(os.path.join("backlog", "1", str(item_id)))


def test_tampered_stored_path_is_refused(db, tmp_path):
    root = str(tmp_path / "art")
    item_id = persistence.create_item(db, 1, text="a")
    att_id = persistence.add_attachment(db, item_id, "FILE", "x", "../../etc/passwd")
    with pytest.raises(ArtifactPathError):
        attachments.attachment_file(root, persistence.get_attachment(db, item_id, att_id))


def test_fresh_database_has_indexes(db):
    names = {r["name"] for r in db.execute("PRAGMA index_list(backlog_items)")}
    assert {"idx_backlog_items_project_status", "idx_backlog_items_priority", "idx_backlog_items_sprint"} <= names


def test_project_delete_cascades(db):
    item_id = persistence.create_item(db, 1, text="a")
    db.execute("DELETE FROM projects WHERE id = 1")
    db.commit()
    assert persistence.get_item(db, item_id) is None
