"""Backlog research action (docs/SPRINT_PLANNING_AND_BACKLOG.md §50, task 44).

Drives `ResearchAgent` directly from a Backlog item -- an ad-hoc, one-off
trigger, the same way `app/sprints/planning_agent.PlanningAgent` is driven
directly from sprint views rather than through a pipeline execution. A
RESEARCH *pipeline step* (task 43, `app/pipelines/engine.py._h_research`) is
for research that is part of a defined pipeline; this module is for "research
this item" from the Backlog UI, which has no pipeline around it.

Adapter selection reuses `app.pipelines.manager.default_agent_factory` (keyed
off the same `PLANNING_AGENT` config Sprint planning and RESEARCH pipeline
steps already use for any ad-hoc/role-based agent session -- there is no
separate `RESEARCH_AGENT` setting anywhere in this codebase) and
`app.sprints.planning_agent.build_context` (repository resolution: the
project's primary repository, falling back to its first repository, falling
back to the first allowed root -- identical to `app/sprints/views.py._context`).

Every adapter today (Codex/Claude/Fake) resolves synchronously inside
`start()` (`app/execution/host.py`'s `execute()` waits for the process to
exit before returning), so `ResearchAgent.research()` blocks for the whole
research pass. Sprint planning's `POST /plan` route already accepts that
tradeoff for the same reason (`app/sprints/views.py.plan`); this route does
the same rather than inventing a background-job mechanism this app does not
otherwise have for ad-hoc agent actions.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app.agents import models as agent_models
from app.agents.research_agent import (
    DEFAULT_COST_LIMIT_USD,
    DEFAULT_TIME_LIMIT_SECONDS,
    ResearchAgent,
    ResearchOutcome,
    scripted_report,
)
from app.agents.research_report import Finding, ResearchReport, Source
from app.artifacts import collector
from app.backlog import attachments, persistence
from app.backlog.models import BacklogItem
from app.pipelines.manager import default_agent_factory
from app.sprints.planning_agent import build_context

logger = logging.getLogger(__name__)

# Per the task spec: a research pass is bounded tighter than the general
# RESEARCH-role defaults (DEFAULT_TIME_LIMIT_SECONDS=300/DEFAULT_COST_LIMIT_USD=10)
# -- a backlog item's research question is smaller in scope than an arbitrary
# pipeline-authored one, so a lower cost ceiling is appropriate; the time
# limit stays at the shared default (still a single research pass, not an
# iteration loop).
TIME_LIMIT_SECONDS = DEFAULT_TIME_LIMIT_SECONDS
COST_LIMIT_USD = 5.0


@dataclass
class ResearchRunResult:
    research_session_id: int | None
    link_id: int | None
    artifact_id: int | None
    outcome: ResearchOutcome


def build_question(item: BacklogItem) -> str:
    """The RESEARCH question: the item's title and body, verbatim -- no
    template beyond what `ResearchAgent.build_prompt` already wraps it in."""
    title = item.title.strip()
    text = item.text.strip()
    if title and text:
        return f"{title}\n\n{text}"
    return title or text or f"Backlog item #{item.id}"


def _kind(app_config) -> str:
    return str(app_config.get("PLANNING_AGENT", "codex")).lower()


def _provider(app_config):
    from app.execution.host import HostExecutionProvider

    return HostExecutionProvider(app_config["DATABASE_PATH"], app_config["ALLOWED_PROJECT_ROOTS"])


def build_agent(app_config, db) -> ResearchAgent:
    provider = _provider(app_config)
    adapter = default_agent_factory(app_config, provider)(db)
    patterns = tuple(app_config.get("REDACT_PATTERNS", ()))
    return ResearchAgent(adapter, db=db, extra_patterns=patterns)


def build_agent_context(app_config, project, db):
    """Run in the project's primary repository (or its first repository, or
    the first allowed root for a repository-less project) -- identical
    resolution to `app/sprints/views.py._context`."""
    repo = next((r for r in project.repositories if r.is_primary), None) or (
        project.repositories[0] if project.repositories else None
    )
    path = repo.path if repo else app_config["ALLOWED_PROJECT_ROOTS"][0]
    return build_context(app_config, project.id, path, db)


def _fake_report(item: BacklogItem) -> ResearchReport:
    """Deterministic report for `PLANNING_AGENT=fake` (automated tests): one
    verified finding citing the item's own text as its source, and one
    unverified finding, so both badge states render without needing a real
    CLI -- the same idea as `PlanningAgent`'s `fake_script_from_items`."""
    subject = item.title or item.text or f"item #{item.id}"
    return ResearchReport(
        summary=f"Automated research summary for: {subject}"[:500],
        findings=[
            Finding(text=f"The backlog item describes: {subject}"[:500], source_ids=["S1"]),
            Finding(text="No further repository evidence was available in this deterministic run."),
        ],
        sources=[Source(id="S1", file_path="backlog_item.txt", line_range="", excerpt=(item.text or subject)[:200])],
    )


def _register_artifact(
    db, root: str, project_id: int, item: BacklogItem, report: ResearchReport,
    research_session_id: int | None, patterns: tuple[str, ...],
) -> int | None:
    """Index the parsed report JSON in the Artifact Library
    (kind='research_report'), the same helper and shape
    `PipelineEngine._register_research_artifact` uses for RESEARCH pipeline
    steps -- tagged with the backlog item (no `execution_id`/`step_execution_id`:
    this research run has no pipeline execution behind it). A collection
    problem never fails the action, matching the pipeline engine's own
    artifact registration."""
    try:
        row = agent_models.get_research_session(db, research_session_id) if research_session_id else None
        content = report.to_json().encode("utf-8")
        metadata = {
            "backlog_item_id": item.id,
            "summary_length": len(report.summary),
            "finding_count": len(report.findings),
            "source_count": len(report.sources),
            "unverified_finding_count": len(report.unverified),
            "cost_usd": row.cost_usd if row else 0.0,
            "duration_seconds": row.duration_seconds if row else None,
        }
        directory = os.path.join(attachments.item_directory(project_id, item.id), "research")
        return collector.store_bytes(
            db, root, project_id, f"backlog_item_{item.id}_research.json", content, directory, patterns,
            kind="research_report", extra_metadata=metadata, mime_type="application/json",
            step_name="backlog_research", tags=[f"backlog-item-{item.id}"],
        )
    except Exception:
        logger.exception("Failed to index research report for backlog item %s", item.id)
        return None


def run_item_research(app_config, db, root: str, project, item: BacklogItem) -> ResearchRunResult:
    """Ask a RESEARCH-role agent about `item`, wait for it to finish (or its
    limits to trip), index the report as an Artifact, and record a
    `backlog_research_links` row. `item.status` is never touched -- research
    can be requested from any Backlog status (§50)."""
    kind = _kind(app_config)
    agent = build_agent(app_config, db)
    context = build_agent_context(app_config, project, db)
    question = build_question(item)
    options = {"script": scripted_report(_fake_report(item))} if kind == "fake" else None

    research_session_id, outcome = agent.research(
        context, question, time_limit_seconds=TIME_LIMIT_SECONDS, cost_limit_usd=COST_LIMIT_USD, options=options,
    )

    artifact_id = None
    if outcome.state == "COMPLETE" and outcome.report is not None:
        patterns = tuple(app_config.get("REDACT_PATTERNS", ()))
        artifact_id = _register_artifact(db, root, project.id, item, outcome.report, research_session_id, patterns)

    link_id = None
    if research_session_id is not None:
        link_id = persistence.add_research_link(db, item.id, research_session_id, artifact_id, outcome.state)

    return ResearchRunResult(
        research_session_id=research_session_id, link_id=link_id, artifact_id=artifact_id, outcome=outcome,
    )


def item_research_history(db, item_id: int) -> list[dict]:
    """Every research run for `item_id`, newest first, each with its parsed
    report (from `research_sessions.report_json`, the same JSON a
    `ResearchAgent.research()` call already stored -- no need to re-read the
    Artifact file just to render this page)."""
    out = []
    for link in persistence.list_research_links(db, item_id):
        session_row = agent_models.get_research_session(db, link.research_session_id)
        report = None
        if session_row and session_row.report_json:
            try:
                report = ResearchReport.from_json(session_row.report_json)
            except ValueError:
                report = None
        out.append({"link": link, "session": session_row, "report": report})
    return out
