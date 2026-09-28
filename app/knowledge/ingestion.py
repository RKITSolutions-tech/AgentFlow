"""Feed a completed RESEARCH session's findings into the shared knowledge
base (task 50.5, docs/RUN_AND_RALPH.md §22/§23).

Each sourced finding becomes one `unverified` `KnowledgeEntry` (task 48's
existing review state -- promoted to `reviewed` from the /knowledge admin UI
before it is ever surfaced to another RESEARCH session's prompt or the
wiki's topic tree, app/knowledge/lookup.py's `confidence="reviewed"` filter).
An unsourced finding is skipped: `research_agent.parse_report`'s own rule is
that a claim with no source id is not a checked fact, so it is not worth
persisting as a suggested KB entry either.
"""
from __future__ import annotations

import sqlite3

from app.agents.research_report import ResearchReport
from app.knowledge import models


def ingest_research_report(
    db: sqlite3.Connection,
    project_id: int | None,
    session_id: int | None,
    report: ResearchReport,
) -> list[int]:
    """Create one unverified KnowledgeEntry per sourced finding. Returns the
    created entry ids."""
    sources_by_id = {s.id: s for s in report.sources}
    created = []
    for finding in report.findings:
        if not finding.source_ids:
            continue
        cited = [sources_by_id[sid] for sid in finding.source_ids if sid in sources_by_id]
        content = finding.text
        if cited:
            refs = "\n".join(
                f"- {s.file_path}" + (f":{s.line_range}" if s.line_range else "") for s in cited
            )
            content = f"{content}\n\nSources:\n{refs}"
        title = finding.text.strip().splitlines()[0][:120] or "Untitled finding"
        entry_id = models.create_entry(
            db, "note", title, content,
            source_project_id=project_id,
            source_session_id=session_id,
            confidence="unverified",
            tags=[finding.category] if finding.category else None,
        )
        created.append(entry_id)
    return created
