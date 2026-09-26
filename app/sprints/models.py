from __future__ import annotations

from dataclasses import dataclass, field

# Lifecycle from docs/SPRINT_PLANNING_AND_BACKLOG.md §10.
SPRINT_STATUSES = (
    "DRAFT",
    "PLANNING",
    "REVIEW",
    "READY",
    "EXECUTING",
    "VERIFYING",
    "COMPLETE",
    "CANCELLED",
)
PLANNING_PROFILES = (
    "QUICK_FIX",
    "STANDARD_FEATURE",
    "MAJOR_FEATURE",
    "REFACTOR",
    "MIGRATION",
    "EXPERIMENT",
)
# Provenance of a proposed work item (§9): agent output is only SUGGESTED
# until a human reviews it.
PROPOSAL_STATES = ("SUGGESTED", "REVIEWED", "APPROVED", "REJECTED")

SPRINT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("PLANNING", "CANCELLED"),
    "PLANNING": ("REVIEW", "DRAFT", "CANCELLED"),
    "REVIEW": ("PLANNING", "READY", "CANCELLED"),
    "READY": ("REVIEW", "EXECUTING", "CANCELLED"),
    "EXECUTING": ("VERIFYING", "CANCELLED"),
    "VERIFYING": ("COMPLETE", "EXECUTING", "CANCELLED"),
    "COMPLETE": (),
    "CANCELLED": (),
}


# Canonical Task states (§27). `PlannedWorkItem.status` is the *proposal*
# state; `task_state` is where the approved Task is in the execution queue.
TASK_STATES = (
    "DRAFT", "READY_FOR_REVIEW", "READY", "RELEASED", "IN_PROGRESS",
    "BLOCKED", "FAILED", "COMPLETE", "CANCELLED",
)
TASK_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("READY_FOR_REVIEW", "READY", "CANCELLED"),
    "READY_FOR_REVIEW": ("DRAFT", "READY", "CANCELLED"),
    "READY": ("DRAFT", "RELEASED", "CANCELLED"),
    "RELEASED": ("READY", "IN_PROGRESS", "CANCELLED"),
    "IN_PROGRESS": ("COMPLETE", "BLOCKED", "FAILED", "RELEASED", "CANCELLED"),
    "BLOCKED": ("RELEASED", "IN_PROGRESS", "FAILED", "CANCELLED"),
    "FAILED": ("RELEASED", "CANCELLED"),
    "COMPLETE": (),
    "CANCELLED": (),
}


class InvalidTaskTransitionError(ValueError):
    """Raised when a Task state change is not permitted by TASK_TRANSITIONS."""


class InvalidSprintTransitionError(ValueError):
    """Raised when a Sprint status change is not permitted by SPRINT_TRANSITIONS."""


class NotReadyError(ValueError):
    """Raised when approval is attempted while a required readiness check fails."""


@dataclass
class Sprint:
    id: int
    project_id: int
    name: str
    goal: str
    description: str
    status: str
    planning_profile: str
    start_date: str | None
    target_date: str | None
    planning_session_id: int | None
    approved_at: str | None
    approved_by: str | None
    created_at: str
    updated_at: str
    auto_run: bool = False


@dataclass
class DocumentRef:
    id: int
    sprint_id: int
    reference: str
    note: str
    created_at: str


@dataclass
class PlannedWorkItem:
    """A proposed Task (§17). Not executable until the Sprint is approved."""

    id: int
    sprint_id: int
    seq: int
    title: str
    description: str
    acceptance: list[str]
    estimate: str
    status: str
    task_state: str = "DRAFT"
    released_at: str | None = None
    verification_pipeline: str = ""
    depends_on: list[int] = field(default_factory=list)
    backlog_item_ids: list[int] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ReadinessCheck:
    id: int
    sprint_id: int
    check_name: str
    status: str  # PASS | FAIL
    details: str
    checked_at: str


@dataclass
class Approval:
    id: int
    sprint_id: int
    decision: str  # APPROVED | REVOKED
    approved_by: str
    comment: str
    created_at: str
