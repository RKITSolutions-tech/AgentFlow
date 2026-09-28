import pytest

from app.db import get_db
from app.knowledge import models


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


def test_slug_validation_rejects_injection(db):
    with pytest.raises(models.InvalidSlugError):
        models.create_entry(db, "note", "t", "c", slug="../../../etc/passwd")
    with pytest.raises(models.InvalidSlugError):
        models.create_entry(db, "note", "t", "c", slug="a/b")
    with pytest.raises(models.InvalidSlugError):
        models.create_entry(db, "note", "t", "c", slug="a;DROP TABLE knowledge_entries;")


def test_wiki_entry_respects_scope_filtering(db):
    models.create_entry(db, "note", "Alpha", "c", scope="project:alpha", slug="alpha-note")
    models.create_entry(db, "note", "Beta", "c", scope="project:beta", slug="beta-note")
    alpha_only = models.list_entries(db, scope="project:alpha")
    assert [e.title for e in alpha_only] == ["Alpha"]


def test_slug_route_rejects_traversal_style_lookup(db):
    models.create_entry(db, "note", "Safe", "c", slug="safe-entry")
    assert models.get_entry_by_slug(db, "../../../etc/passwd") is None
