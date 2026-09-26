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
