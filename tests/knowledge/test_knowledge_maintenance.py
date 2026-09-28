import pytest

from app.db import get_db
from app.knowledge import maintenance, models


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


def test_read_logging_increments_use_count(db):
    entry_id = models.create_entry(db, "note", "T", "c", slug="t", confidence="reviewed")
    for _ in range(5):
        models.log_read(db, entry_id, "test")
    assert models.get_entry(db, entry_id).use_count == 5


def test_stale_entry_detection(db):
    entry_id = models.create_entry(db, "note", "Old", "c", slug="old", confidence="reviewed", freshness_days=30)
    db.execute(
        "UPDATE knowledge_entries SET last_read_at = datetime('now', '-40 days') WHERE id = ?", (entry_id,)
    )
    db.commit()
    fresh_id = models.create_entry(db, "note", "Fresh", "c", slug="fresh", confidence="reviewed", freshness_days=30)
    models.log_read(db, fresh_id, "test")

    stale = maintenance.stale_entries(db)
    assert entry_id in [e.id for e in stale]
    assert fresh_id not in [e.id for e in stale]


def test_unused_entry_detection(db):
    unused_id = models.create_entry(db, "note", "Unused", "c", slug="unused", confidence="reviewed")
    used_id = models.create_entry(db, "note", "Used", "c", slug="used", confidence="reviewed")
    models.log_read(db, used_id, "test")

    unused = maintenance.unused_entries(db)
    assert unused_id in [e.id for e in unused]
    assert used_id not in [e.id for e in unused]


def test_duplicate_detection_via_similarity(db):
    base = "The quick brown fox jumps over the lazy dog near the river bank at dawn."
    models.create_entry(db, "note", "A", base, slug="dup-a", confidence="reviewed")
    models.create_entry(db, "note", "B", base + " ", slug="dup-b", confidence="reviewed")
    models.create_entry(db, "note", "C", "Completely unrelated content about databases.", slug="dup-c", confidence="reviewed")

    pairs = maintenance.potential_duplicates(db)
    titles = {frozenset((a.title, b.title)) for a, b, _ in pairs}
    assert frozenset({"A", "B"}) in titles
    assert not any("C" in pair for pair in titles)


def test_missing_backlinks_suggestion(db):
    models.create_entry(db, "note", "Auth JWT", "how to auth", slug="auth-jwt", confidence="reviewed")
    models.create_entry(db, "note", "Guide", "See the Auth JWT approach for details.", slug="guide", confidence="reviewed")

    suggestions = maintenance.missing_backlinks(db)
    assert any(entry.slug == "guide" and target == "auth-jwt" for entry, target in suggestions)


def test_generate_report_combines_all_sections(db):
    models.create_entry(db, "note", "T", "c", slug="t", confidence="reviewed")
    report = maintenance.generate_report(db)
    assert hasattr(report, "stale")
    assert hasattr(report, "unused")
    assert hasattr(report, "duplicates")
    assert hasattr(report, "missing_backlinks")


def test_review_queue_page_renders_slugless_stale_and_unused_entries(app, client, db):
    """A reviewed entry with no slug (e.g. RESEARCH ingestion, task 50.5, never
    assigns one) can still be stale/unused -- the review queue page must link
    to it via the /knowledge admin view instead of crashing on url_for('wiki.
    view_entry', slug=None)."""
    models.create_entry(db, "note", "Slugless unused", "content", confidence="reviewed")
    response = client.get("/wiki/review-queue")
    assert response.status_code == 200
    assert b"Slugless unused" in response.data
