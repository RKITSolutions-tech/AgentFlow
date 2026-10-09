"""Full-text search across a project's wiki roots
(docs/WIKI_INTEGRATION_AND_PRESENTATION.md §4.3, §9 Option B).

An in-memory inverted index `{term: {(root key, path): weighted count}}` is
built from the scanner's per-file term counts and rebuilt only when the set of
(page, mtime) pairs changes. A page matches when every query term occurs in it
(as a word, or as a prefix of one for terms of 3+ characters, or in its path).
Score = sum over terms of count x idf (title words count triple), plus a path
bonus, times `1 + recency_bonus` (up to +50% for pages changed today, fading
to nothing over a year). Each result carries an escaped snippet around the
first match with the terms wrapped in `<mark>`.
"""
from __future__ import annotations

import html
import math
import re
import threading
import time
from dataclasses import dataclass

from app.wikis import scanner
from app.wikis.models import WikiRoot
from app.wikis.scanner import SCANNER

SNIPPET_RADIUS = 90
RECENCY_WINDOW_DAYS = 365
RECENCY_MAX_BONUS = 0.5


@dataclass
class SearchResult:
    root_key: str
    root_label: str
    page_path: str
    title: str
    snippet: str        # safe HTML (escaped text + <mark>)
    score: float
    modified: float
    url: str = ""

    def to_dict(self) -> dict:
        return {
            "root": self.root_key, "root_label": self.root_label, "path": self.page_path,
            "title": self.title, "snippet": self.snippet, "score": round(self.score, 3), "url": self.url,
        }


def _query_terms(query: str) -> list[str]:
    seen: list[str] = []
    for term in scanner.tokenize(query or ""):
        if term not in seen:
            seen.append(term)
    return seen


def _snippet(text: str, terms: list[str]) -> str:
    flat = " ".join(text.split())
    lower = flat.lower()
    positions = [lower.find(t) for t in terms if lower.find(t) >= 0]
    at = min(positions) if positions else 0
    start = max(0, at - SNIPPET_RADIUS)
    end = min(len(flat), at + SNIPPET_RADIUS * 2)
    piece = html.escape(flat[start:end])
    for term in sorted(terms, key=len, reverse=True):
        piece = re.sub(f"({re.escape(html.escape(term))})", r"<mark>\1</mark>", piece, flags=re.I)
    return ("&hellip;" if start else "") + piece + ("&hellip;" if end < len(flat) else "")


class WikiSearchEngine:
    def __init__(self):
        self._lock = threading.Lock()
        # set of roots -> (page/mtime signature it was built from, inverted index)
        self._indexes: dict[str, tuple[tuple, dict[str, dict[tuple, int]]]] = {}

    def _index(self, roots: list[WikiRoot]) -> tuple[dict[str, dict[tuple, int]], dict[tuple, tuple]]:
        pages = {}
        for root in roots:
            for path, meta in SCANNER.scan_root(root).items():
                pages[(root.key, path)] = (root, meta)
        signature = tuple(sorted((k, m.modified, m.size) for k, (_, m) in pages.items()))
        cache_key = "|".join(sorted(r.key + "@" + r.path for r in roots))
        with self._lock:
            cached = self._indexes.get(cache_key)
            if cached and cached[0] == signature:
                return cached[1], pages
        inverted: dict[str, dict[tuple, int]] = {}
        for key, (root, meta) in pages.items():
            for term, count in SCANNER.terms(root, meta.path).items():
                inverted.setdefault(term, {})[key] = count
        with self._lock:
            self._indexes[cache_key] = (signature, inverted)
        return inverted, pages

    def search(self, roots: list[WikiRoot], query: str, limit: int = 30, now: float | None = None) -> list[SearchResult]:
        terms = _query_terms(query)
        if not terms or not roots:
            return []
        inverted, pages = self._index(roots)
        total = max(1, len(pages))
        scores: dict[tuple, float] | None = None
        for term in terms:
            postings: dict[tuple, float] = {}
            exact = inverted.get(term, {})
            if len(term) >= 3:
                for indexed, docs in inverted.items():
                    if indexed != term and indexed.startswith(term):
                        for key, count in docs.items():
                            postings[key] = postings.get(key, 0) + count * 0.5
            for key, count in exact.items():
                postings[key] = postings.get(key, 0) + count
            for key in pages:
                if term in key[1].lower():
                    postings[key] = postings.get(key, 0) + 5
            idf = math.log(1 + total / (1 + len(postings)))
            term_scores = {k: v * idf for k, v in postings.items()}
            if scores is None:
                scores = term_scores
            else:
                scores = {k: scores[k] + term_scores[k] for k in scores.keys() & term_scores.keys()}
            if not scores:
                return []
        now = now or time.time()
        results = []
        for key, base in (scores or {}).items():
            root, meta = pages[key]
            age_days = max(0.0, (now - meta.modified) / 86400)
            recency = RECENCY_MAX_BONUS * max(0.0, 1 - age_days / RECENCY_WINDOW_DAYS)
            text = SCANNER.text(root, meta.path) or ""
            results.append(SearchResult(
                root_key=root.key, root_label=root.label, page_path=meta.path, title=meta.title,
                snippet=_snippet(scanner.parse_frontmatter(text)[1], terms), score=base * (1 + recency),
                modified=meta.modified,
            ))
        results.sort(key=lambda r: (-r.score, r.title.lower()))
        return results[:limit]

    def invalidate(self) -> None:
        with self._lock:
            self._indexes.clear()


ENGINE = WikiSearchEngine()
