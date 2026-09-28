"""Knowledge lookup service for RESEARCH agent integration."""
from __future__ import annotations

import sqlite3

from app.knowledge import models, search


class KnowledgeLookup:
    """Query knowledge base for research context."""

    def __init__(self, db: sqlite3.Connection, project_id: int | None = None):
        self._db = db
        self._project_id = project_id
        self._searcher = search.KnowledgeSearch()

    def research(
        self,
        question: str,
        limit: int = 5,
    ) -> list[models.KnowledgeEntry]:
        """Query knowledge base for entries relevant to a research question."""
        results = self._searcher.search(
            self._db,
            question,
            confidence="reviewed",
            limit=limit,
        )
        entries = []
        for r in results:
            entry = models.get_entry(self._db, r.entry_id)
            if entry:
                models.increment_use_count(self._db, r.entry_id)
                entries.append(entry)
        return entries
