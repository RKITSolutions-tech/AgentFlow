"""Full-text search for knowledge entries."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.knowledge import models


@dataclass
class SearchResult:
    entry_id: int
    title: str
    confidence: str
    relevance_score: float
    use_count: int


class KnowledgeSearch:
    """Full-text search over knowledge entries."""

    def search(
        self,
        db: sqlite3.Connection,
        query: str,
        kind: str | None = None,
        confidence: str | None = None,
        scope: str | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Search knowledge base with filters, return ranked results."""
        sql = "SELECT id, title, confidence, use_count FROM knowledge_entries WHERE 1=1"
        params = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if confidence:
            sql += " AND confidence = ?"
            params.append(confidence)
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        if query:
            sql += " AND (title LIKE ? OR content LIKE ?)"
            like_query = f"%{query}%"
            params.extend([like_query, like_query])
        if tags:
            placeholders = ",".join("?" * len(tags))
            sql += f" AND id IN (SELECT entry_id FROM knowledge_tags WHERE tag IN ({placeholders}))"
            params.extend(tags)
        sql += " ORDER BY use_count DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        rows = db.execute(sql, params).fetchall()
        return [
            SearchResult(
                entry_id=r["id"],
                title=r["title"],
                confidence=r["confidence"],
                relevance_score=1.0 if query in r["title"] else 0.5,
                use_count=r["use_count"],
            )
            for r in rows
        ]
