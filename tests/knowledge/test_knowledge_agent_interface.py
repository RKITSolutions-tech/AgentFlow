import pytest

from app.db import get_db
from app.knowledge import models
from app.knowledge.agent_interface import WikiSearcher


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


@pytest.fixture
def searcher(db):
    return WikiSearcher(db)


def test_search_skill_returns_structured_result(db, searcher):
    models.create_entry(db, "note", "JWT auth", "how to do jwt auth in python", slug="auth-jwt")
    results = searcher.search("jwt")
    assert results
    r = results[0]
    assert set(r.keys()) >= {"slug", "title", "summary", "snippet", "relevance_score"}
    assert r["slug"] == "auth-jwt"


def test_get_skill_with_section_filter(db, searcher):
    content = "# Overview\nintro text\n\n## Implementation\nthe impl details\n\n## Testing\ntest notes"
    models.create_entry(db, "note", "Auth JWT", content, slug="auth-jwt")
    result = searcher.get("auth-jwt", section="Implementation")
    assert result["content"] == "the impl details"
    assert result["slug"] == "auth-jwt"


def test_get_skill_missing_slug_returns_none(db, searcher):
    assert searcher.get("does-not-exist") is None


def test_get_skill_logs_read(db, searcher):
    entry_id = models.create_entry(db, "note", "T", "c", slug="t")
    searcher.get("t")
    entry = models.get_entry(db, entry_id)
    assert entry.use_count == 1


def test_list_topics_hierarchy(db, searcher):
    models.create_entry(
        db, "note", "Py testing", "c", slug="py-testing", confidence="reviewed",
        language="python", library="pytest", version="8.x",
    )
    tree = searcher.list_topics()
    assert "python" in tree
    assert "pytest" in tree["python"]


def test_related_skill_uses_backlink_graph(db, searcher):
    a = models.create_entry(db, "note", "Entry A", "See [[entry-b]]", slug="entry-a")
    models.create_entry(db, "note", "Entry B", "no links", slug="entry-b")
    related = searcher.related("entry-b")
    assert any(r["slug"] == "entry-a" and r["reason_related"] == "links_to_this" for r in related)


def test_propose_skill_creates_review_queue_item(db, searcher):
    result = searcher.propose("auth-jwt", "update", "new content", title="Auth JWT", reason="found a gap")
    assert result["status"] == "queued"
    pending = models.list_review_queue(db, status="pending")
    assert any(i.id == result["review_item_id"] for i in pending)
