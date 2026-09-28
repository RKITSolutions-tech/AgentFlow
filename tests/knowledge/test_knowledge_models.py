import pytest

from app.db import get_db
from app.knowledge import models


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


def test_create_entry_with_slug_and_retrieve_by_slug(db):
    entry_id = models.create_entry(
        db, "note", "Auth via JWT", "Use jwt for auth", slug="auth-jwt",
        aliases=["jwt-auth"], topic="auth", language="python", library="flask",
    )
    entry = models.get_entry(db, entry_id)
    assert entry.slug == "auth-jwt"
    assert entry.aliases == ["jwt-auth"]
    by_slug = models.get_entry_by_slug(db, "auth-jwt")
    assert by_slug.id == entry_id
    by_alias = models.get_entry_by_slug(db, "jwt-auth")
    assert by_alias.id == entry_id


def test_slug_validation_rejects_bad_characters(db):
    with pytest.raises(models.InvalidSlugError):
        models.create_entry(db, "note", "t", "c", slug="Has Spaces")
    with pytest.raises(models.InvalidSlugError):
        models.create_entry(db, "note", "t", "c", slug="../../../etc/passwd")
    with pytest.raises(models.InvalidSlugError):
        models.create_entry(db, "note", "t", "c", slug="-leading-hyphen")


def test_duplicate_slug_rejected(db):
    models.create_entry(db, "note", "One", "c", slug="dupe")
    with pytest.raises(ValueError):
        models.create_entry(db, "note", "Two", "c", slug="dupe")


def test_backlink_extraction_from_content(db):
    a = models.create_entry(db, "note", "Entry A", "See [[entry-b]] for details", slug="entry-a")
    models.create_entry(db, "note", "Entry B", "no links here", slug="entry-b")
    backlinks = models.list_backlinks(db, "entry-b")
    assert len(backlinks) == 1
    assert backlinks[0].source_entry_id == a
    assert models.list_forward_links(db, a) == ["entry-b"]


def test_backlinks_resync_on_update(db):
    a = models.create_entry(db, "note", "Entry A", "See [[entry-b]]", slug="entry-a")
    models.update_entry(db, a, content="now links to [[entry-c]] instead")
    assert models.list_backlinks(db, "entry-b") == []
    assert models.list_forward_links(db, a) == ["entry-c"]


def test_read_log_increments_use_count_and_last_read(db):
    entry_id = models.create_entry(db, "note", "T", "C", slug="t")
    for _ in range(5):
        models.log_read(db, entry_id, context="agent session")
    entry = models.get_entry(db, entry_id)
    assert entry.use_count == 5
    assert entry.last_read_at is not None


def test_list_topics_hierarchy(db):
    models.create_entry(
        db, "note", "Py testing", "c", slug="py-testing", confidence="reviewed",
        language="python", library="pytest", version="8.x", topic="testing",
    )
    tree = models.list_topics(db)
    assert "python" in tree
    assert "pytest" in tree["python"]
    assert "8.x" in tree["python"]["pytest"]
    assert tree["python"]["pytest"]["8.x"][0]["slug"] == "py-testing"


def test_propose_change_creates_review_queue_item(db):
    item_id = models.propose_change(
        db, "create", slug="new-topic", title="New topic", content="body", reason="gap found",
    )
    pending = models.list_review_queue(db, status="pending")
    assert any(i.id == item_id for i in pending)


def test_resolve_review_item_approve_and_reject(db):
    item_id = models.propose_change(db, "create", slug="x", title="X", content="c")
    models.resolve_review_item(db, item_id, "approved", "sam")
    item = models.get_review_item(db, item_id)
    assert item.status == "approved"
    assert item.reviewed_by == "sam"
    assert models.list_review_queue(db, status="pending") == []
