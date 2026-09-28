"""Agent-facing wiki skill interface (task 50.2).

`WikiSearcher` is the RESEARCH-role analogue of `app.knowledge.lookup.
KnowledgeLookup` (task 48), but exposes the richer, agent-callable surface
the wiki layer needs: structured dict in/out methods (`search`, `get`,
`list_topics`, `related`, `propose`) rather than a single `research()`
convenience call. It is a plain Python service, called directly by app code
on an agent's behalf (`app/agents/research_agent.py`, `app/ralph/
orchestrator.py`) -- the same pattern as `KnowledgeLookup` and `app.sprints.
planning_agent.PlanningAgent`: no adapter gets a bespoke tool-calling
protocol, so this is just a structured function surface those callers use
before/while building a prompt, and `propose()` is how an agent's *findings*
flow back in without ever writing to the wiki directly.

Registered as a RESEARCH skill (`app.prompts.defaults.DEFAULT_SKILLS`) so
`assembler.skill_context()` tells an agent this interface exists; the actual
calls still happen in Python, not inside the agent's own session.
"""
from __future__ import annotations

import sqlite3

from app.knowledge import models, search


class WikiSearcher:
    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._search = search.KnowledgeSearch()

    def search(self, query: str, filters: dict | None = None, limit: int = 10) -> list[dict]:
        """search(query, filters={kind, language, library, version, confidence,
        freshness, scope}) -> [{slug, title, summary, snippet, relevance_score}]"""
        f = dict(filters or {})
        results = self._search.search(
            self._db,
            query,
            kind=f.get("kind"),
            confidence=f.get("confidence"),
            scope=f.get("scope"),
            language=f.get("language"),
            library=f.get("library"),
            version=f.get("version"),
            freshness_days=f.get("freshness"),
            limit=limit,
        )
        out = []
        for r in results:
            entry = models.get_entry(self._db, r.entry_id)
            if entry is None:
                continue
            out.append({
                "slug": entry.slug,
                "title": entry.title,
                "summary": r.snippet,
                "snippet": r.snippet,
                "relevance_score": r.relevance_score,
            })
        return out

    def get(self, slug: str, section: str | None = None) -> dict | None:
        """get(slug, section=None) -> {slug, title, aliases, content,
        related_entries, backlinks, last_updated}"""
        entry = models.get_entry_by_slug(self._db, slug)
        if entry is None:
            return None
        models.log_read(self._db, entry.id, context="agent:get")
        content = entry.content
        if section:
            content = _extract_section(content, section) or ""
        return {
            "slug": entry.slug,
            "title": entry.title,
            "aliases": entry.aliases,
            "content": content,
            "related_entries": [r["slug"] for r in self.related(slug)],
            "backlinks": [b.source_slug or str(b.source_entry_id) for b in models.list_backlinks(self._db, slug)],
            "last_updated": entry.updated_at,
        }

    def list_topics(self) -> dict:
        """list_topics() -> hierarchical {language: {library: {version: [entries]}}}"""
        return models.list_topics(self._db)

    def related(self, slug: str) -> list[dict]:
        """related(slug) -> [{slug, title, reason_related}] from the backlink graph."""
        out = []
        for b in models.list_backlinks(self._db, slug):
            out.append({"slug": b.source_slug, "title": b.source_title, "reason_related": "links_to_this"})
        entry = models.get_entry_by_slug(self._db, slug)
        if entry is not None:
            for target_slug in models.list_forward_links(self._db, entry.id):
                target = models.get_entry_by_slug(self._db, target_slug)
                if target is not None:
                    out.append({
                        "slug": target.slug, "title": target.title, "reason_related": "linked_from_this",
                    })
        return out

    def propose(self, slug: str | None, change_type: str, content: str, title: str | None = None, reason: str = "") -> dict:
        """propose(slug, change_type, content) -> stores the proposal in the
        review queue; returns {status, review_item_id}."""
        entry = models.get_entry_by_slug(self._db, slug) if slug else None
        item_id = models.propose_change(
            self._db,
            change_type,
            slug=slug,
            entry_id=entry.id if entry else None,
            title=title,
            content=content,
            reason=reason,
        )
        return {"status": "queued", "review_item_id": item_id}


def _extract_section(content: str, section: str) -> str | None:
    """Return the body of the first Markdown heading whose text matches
    `section` (case-insensitive), up to the next heading of equal-or-higher
    level."""
    lines = content.splitlines()
    start = None
    level = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            heading_text = stripped[hashes:].strip()
            if start is None and heading_text.lower() == section.lower():
                start = i + 1
                level = hashes
                continue
            if start is not None and hashes <= level:
                return "\n".join(lines[start:i]).strip()
    if start is not None:
        return "\n".join(lines[start:]).strip()
    return None
