import pytest

from app.db import get_db
from app.knowledge import models, search


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


@pytest.fixture
def searcher():
    return search.KnowledgeSearch()


def _entry(db, title, content, **kw):
    kw.setdefault("confidence", "reviewed")
    return models.create_entry(db, "note", title, content, **kw)


def test_fts5_query_builder_basic_and_field_specific(db, searcher):
    fts_text, tags = search.build_fts_query('title:"auth" tag:python')
    assert tags == ["python"]
    assert "tag:" not in fts_text
    assert 'title:"auth"' in fts_text


def test_fts5_search_with_filters(db, searcher):
    id1 = _entry(db, "Python auth", "jwt auth in python", language="python")
    _entry(db, "Python testing", "pytest fixtures", language="python")
    id3 = _entry(db, "JS auth", "jwt in javascript", language="js")
    id4 = _entry(db, "JS routing", "express routing", language="js")
    id5 = _entry(db, "JS stale", "old note", language="js")
    db.execute("UPDATE knowledge_entries SET last_read_at = '2020-01-01T00:00:00' WHERE id = ?", (id1,))
    db.execute("UPDATE knowledge_entries SET last_read_at = '2000-01-01T00:00:00' WHERE id = ?", (id5,))
    db.commit()
    models.log_read(db, id3, "test")
    models.log_read(db, id4, "test")

    results = searcher.search(db, "", language="js", freshness_days=7)
    slugs = {r.entry_id for r in results}
    assert id3 in slugs and id4 in slugs
    assert len(results) == 2


def test_fts5_snippet_context_window(db, searcher):
    padding = "x" * 200
    content = f"{padding} the quick brown fox jumps over lazy dog {padding}"
    _entry(db, "Snippet test", content)
    results = searcher.search(db, "fox")
    assert results
    assert "fox" in results[0].snippet
    assert len(results[0].snippet) < len(content)


def test_fts5_fuzzy_matching(db, searcher):
    _entry(db, "Authentication guide", "how to authenticate users")
    results = searcher.search(db, "authntication")
    assert any("Authentication" in r.title for r in results)


def test_tag_filter_applies_alongside_fts(db, searcher):
    id1 = _entry(db, "Tagged entry", "content about auth", tags=["security"])
    _entry(db, "Untagged entry", "content about auth")
    results = searcher.search(db, "auth tag:security")
    assert [r.entry_id for r in results] == [id1]


def test_fuzzy_retry_on_multiword_question_does_not_corrupt_or_operator(db, searcher):
    """A natural-language question with a typo is OR-joined (build_fts_query)
    and can hit the fuzzy-retry path (search.py's `_fuzzy_retry`); the
    literal "OR" boolean operator must never itself become a fuzzy-matched
    search term (it would if a vocab word happened to resemble "or"),
    which would corrupt the query's boolean structure."""
    _entry(db, "Authentication guide", "how sessions authenticate for users")
    results = searcher.search(db, "How does authenticaton and sessions work?")
    assert any("Authentication" in r.title for r in results)


def test_malformed_fts_query_returns_no_results_not_error(db, searcher):
    results = searcher.search(db, '"unterminated quote')
    assert results == []
