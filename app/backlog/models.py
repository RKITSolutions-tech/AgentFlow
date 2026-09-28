from __future__ import annotations

from dataclasses import dataclass

# Lifecycle from docs/SPRINT_PLANNING_AND_BACKLOG.md §6.
BACKLOG_STATUSES = (
    "INBOX",
    "TRIAGED",
    "SELECTED",
    "PLANNING",
    "PLANNED",
    "READY",
    "RELEASED",
    "ARCHIVED",
    "REJECTED",
)
BACKLOG_PRIORITIES = ("LOW", "MEDIUM", "HIGH")
ATTACHMENT_KINDS = ("IMAGE", "FILE", "LINK")

# Allowed moves. Later planning stages can fall back a step (e.g. a rejected
# plan returns to SELECTED); ARCHIVED/REJECTED items can be reopened to INBOX.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "INBOX": ("TRIAGED", "SELECTED", "ARCHIVED", "REJECTED"),
    "TRIAGED": ("INBOX", "SELECTED", "ARCHIVED", "REJECTED"),
    "SELECTED": ("TRIAGED", "PLANNING", "ARCHIVED"),
    "PLANNING": ("SELECTED", "PLANNED", "ARCHIVED"),
    "PLANNED": ("PLANNING", "READY", "ARCHIVED"),
    "READY": ("PLANNED", "RELEASED", "ARCHIVED"),
    "RELEASED": ("ARCHIVED",),
    "ARCHIVED": ("INBOX",),
    "REJECTED": ("INBOX",),
}


class InvalidTransitionError(ValueError):
    """Raised when a status change is not permitted by TRANSITIONS."""


@dataclass
class BacklogItem:
    id: int
    project_id: int
    title: str
    text: str
    status: str
    priority: str | None
    sprint_id: int | None
    created_by: str
    source_type: str
    source_reference: str
    created_at: str
    updated_at: str


@dataclass
class BacklogAttachment:
    id: int
    backlog_item_id: int
    kind: str
    name: str
    path: str
    size: int
    created_at: str


@dataclass
class TriageEntry:
    id: int
    backlog_item_id: int
    old_status: str | None
    new_status: str
    notes: str
    changed_by: str
    changed_at: str


# Backlog research action (docs/SPRINT_PLANNING_AND_BACKLOG.md §50, task 44).
RESEARCH_LINK_STATUSES = ("PENDING", "ACCEPTED", "DISMISSED")


@dataclass
class BacklogResearchLink:
    """Links a `research_sessions` row (app/agents/models.ResearchSession) and
    its Artifact Library entry (kind='research_report') back to the Backlog
    item that triggered it. Status change stays with `backlog_items.status`;
    this is a parallel, non-lifecycle trail of research runs for the item."""

    id: int
    backlog_item_id: int
    research_session_id: int
    artifact_id: int | None
    outcome_state: str  # ResearchOutcome.state: COMPLETE | FAILED | TIMED_OUT | COST_LIMIT_EXCEEDED
    status: str  # PENDING | ACCEPTED | DISMISSED
    reviewed_at: str | None
    created_at: str
