"""Full-text search for knowledge/wiki entries (task 48, extended by task 50
with FTS5, field-specific queries and snippets).

FTS5 (app/db.py's `wiki_search` virtual table) natively supports column
filters (`title:jwt`, `content:auth`), so the query builder here only has to
special-case `tag:` -- tags live in the separate `knowledge_tags` join table,
not in the FTS index, so a `tag:` term is pulled out of the raw query and
applied as an ordinary filter alongside `kind`/`confidence`/`scope`/etc.

There is no spellfix1 (or similar fuzzy-match) SQLite extension bundled with
this app, so "fuzzy matching" (`authntication` -> `authentication`) is a
fallback, not FTS5-native: an exact MATCH that returns nothing is retried by
finding the closest title word with `difflib.get_close_matches` and
re-querying with that word instead.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
from dataclasses import dataclass

TAG_TERM_RE = re.compile(r"(?:^|\s)tag:(\S+)")
_QUERY_CACHE_SIZE = 128


@dataclass
class SearchResult:
    entry_id: int
    title: str
    confidence: str
    relevance_score: float
    use_count: int
    snippet: str = ""
    slug: str | None = None


def build_fts_query(query: str) -> tuple[str, list[str]]:
    """Split a raw query into (fts_match_expression, tag_filters).

    `tag:foo` terms are removed from the text handed to FTS5 and returned
    separately; everything else (including native `title:`/`content:`
    column filters) passes through unchanged -- except plain multi-word free
    text (no column filter, quoting or explicit boolean operator), which is
    OR-joined rather than left as FTS5's implicit AND: a caller here is
    typically a natural-language question (`KnowledgeLookup.research`,
    `ResearchAgent._kb_context`), and requiring every word in the question to
    appear in a matching entry -- including "how"/"should"/"the" -- makes a
    relevant entry unfindable far more often than it filters out noise.
    """
    tags = TAG_TERM_RE.findall(query or "")
    fts_text = TAG_TERM_RE.sub(" ", query or "").strip()
    if fts_text and ":" not in fts_text and '"' not in fts_text:
        # Strip punctuation FTS5's bareword syntax doesn't accept (a trailing
        # "?" in a natural-language question is a syntax error, not just a
        # non-match) -- keep only what the default unicode61 tokenizer would
        # itself treat as part of a token.
        words = [w for w in (re.sub(r"[^\w]", "", t) for t in re.split(r"\s+", fts_text)) if w]
        words = [w for w in words if w.upper() not in ("AND", "OR", "NOT")]
        if len(words) > 1:
            fts_text = " OR ".join(words)
        elif words:
            fts_text = words[0]
    return fts_text, tags


def _snippet(content: str, query_words: list[str], window: int = 50) -> str:
    """A window of `content` around the first matching word, +-`window` chars."""
    if not content:
        return ""
    lower = content.lower()
    pos = -1
    for word in query_words:
        pos = lower.find(word.lower())
        if pos != -1:
            break
    if pos == -1:
        return content[: window * 2].strip()
    start = max(0, pos - window)
    end = min(len(content), pos + len(query_words[0] if query_words else "") + window)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{content[start:end].strip()}{suffix}"


class KnowledgeSearch:
    """Full-text search over knowledge/wiki entries, backed by FTS5."""

    def __init__(self):
        # Popular-search cache: maps a cache key (query + filters) to the
        # last result set, evicted LRU-style once it grows past
        # `_QUERY_CACHE_SIZE`. Deliberately process-local and unbounded in
        # staleness (a write doesn't invalidate it) -- entries change rarely
        # enough relative to reads that a short-lived staleness is an
        # acceptable trade for skipping the FTS5 query on a repeat search.
        self._cache: dict[tuple, list[SearchResult]] = {}
        self._cache_order: list[tuple] = []

    def _cache_get(self, key: tuple) -> list[SearchResult] | None:
        return self._cache.get(key)

    def _cache_put(self, key: tuple, value: list[SearchResult]) -> None:
        if key in self._cache:
            self._cache_order.remove(key)
        self._cache[key] = value
        self._cache_order.append(key)
        while len(self._cache_order) > _QUERY_CACHE_SIZE:
            oldest = self._cache_order.pop(0)
            self._cache.pop(oldest, None)

    def search(
        self,
        db: sqlite3.Connection,
        query: str,
        kind: str | None = None,
        confidence: str | None = None,
        scope: str | None = None,
        tags: list[str] | None = None,
        language: str | None = None,
        library: str | None = None,
        version: str | None = None,
        freshness_days: int | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchResult]:
        """Search knowledge base with filters, return ranked results."""
        fts_text, tag_terms = build_fts_query(query)
        all_tags = list(tags or []) + tag_terms
        cache_key = (
            fts_text, kind, confidence, scope, tuple(sorted(all_tags)),
            language, library, version, freshness_days, limit, offset,
        )
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        params: list = []
        sql = (
            "SELECT e.id, e.title, e.confidence, e.use_count, e.content, e.slug, "
            "bm25(wiki_search) AS rank "
            "FROM knowledge_entries e JOIN wiki_search ON wiki_search.rowid = e.id "
            "WHERE 1=1"
        )
        if fts_text:
            sql += " AND wiki_search MATCH ?"
            params.append(fts_text)
        sql += self._filter_clause(kind, confidence, scope, language, library, version, freshness_days, params)
        if all_tags:
            placeholders = ",".join("?" * len(all_tags))
            sql += f" AND e.id IN (SELECT entry_id FROM knowledge_tags WHERE tag IN ({placeholders}))"
            params.extend(all_tags)
        sql += " ORDER BY rank LIMIT ? OFFSET ?" if fts_text else " ORDER BY e.use_count DESC, e.updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._safe_execute(db, sql, params)
        if not rows and fts_text:
            retried = self._fuzzy_retry(db, fts_text, kind, confidence, scope, language, library, version,
                                         freshness_days, all_tags, limit, offset)
            if retried is not None:
                rows = retried

        words = [w for w in re.split(r"\s+", fts_text) if w and ":" not in w]
        results = [
            SearchResult(
                entry_id=r["id"],
                title=r["title"],
                confidence=r["confidence"],
                relevance_score=(1.0 / (1.0 + abs(r["rank"]))) if "rank" in r.keys() and r["rank"] is not None else 0.5,
                use_count=r["use_count"],
                snippet=_snippet(r["content"], words) if words else (r["content"] or "")[:100],
                slug=r["slug"],
            )
            for r in rows
        ]
        self._cache_put(cache_key, results)
        return results

    def _filter_clause(self, kind, confidence, scope, language, library, version, freshness_days, params: list) -> str:
        clause = ""
        if kind:
            clause += " AND e.kind = ?"
            params.append(kind)
        if confidence:
            clause += " AND e.confidence = ?"
            params.append(confidence)
        if scope:
            clause += " AND e.scope = ?"
            params.append(scope)
        if language:
            clause += " AND e.language = ?"
            params.append(language)
        if library:
            clause += " AND e.library = ?"
            params.append(library)
        if version:
            clause += " AND e.version = ?"
            params.append(version)
        if freshness_days is not None:
            clause += " AND e.last_read_at IS NOT NULL AND julianday('now') - julianday(e.last_read_at) <= ?"
            params.append(freshness_days)
        return clause

    def _safe_execute(self, db: sqlite3.Connection, sql: str, params: list):
        try:
            return db.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # Malformed FTS5 query syntax (e.g. an unmatched quote in free text)
            # -- treat as "no results" rather than a 500.
            return []

    def _fuzzy_retry(
        self, db, fts_text, kind, confidence, scope, language, library, version, freshness_days, all_tags, limit, offset,
    ):
        """No exact match: try the closest known title word instead."""
        vocab = {
            word.lower()
            for (title,) in db.execute("SELECT title FROM knowledge_entries")
            for word in re.findall(r"[a-zA-Z0-9]+", title)
        }
        if not vocab:
            return None
        replaced = False
        new_terms = []
        for term in fts_text.split():
            if term.upper() in ("AND", "OR", "NOT"):
                new_terms.append(term)
                continue
            bare = term.split(":")[-1]
            match = difflib.get_close_matches(bare.lower(), vocab, n=1, cutoff=0.75)
            if match and match[0] != bare.lower():
                new_terms.append(term.replace(bare, match[0]))
                replaced = True
            else:
                new_terms.append(term)
        if not replaced:
            return None
        params: list = [" ".join(new_terms)]
        sql = (
            "SELECT e.id, e.title, e.confidence, e.use_count, e.content, e.slug, "
            "bm25(wiki_search) AS rank "
            "FROM knowledge_entries e JOIN wiki_search ON wiki_search.rowid = e.id "
            "WHERE wiki_search MATCH ?"
        )
        sql += self._filter_clause(kind, confidence, scope, language, library, version, freshness_days, params)
        if all_tags:
            placeholders = ",".join("?" * len(all_tags))
            sql += f" AND e.id IN (SELECT entry_id FROM knowledge_tags WHERE tag IN ({placeholders}))"
            params.extend(all_tags)
        sql += " ORDER BY rank LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return self._safe_execute(db, sql, params)
