"""Acceptance workflow: approval, verification, evidence suggestion and the
completion gate (docs/RUN_AND_RALPH.md §14-§15, SPRINT_PLANNING §21)."""
from __future__ import annotations

import re
import sqlite3

from app.acceptance import models, templates
from app.acceptance.models import AcceptanceError, Criterion
from app.artifacts import models as artifact_models
from app.pipelines import executions

_STOPWORDS = frozenset(
    "the and for with that this from have has are was were will shall should must when then than into onto "
    "each all any none not can use uses used using its their there here about over under after before "
    "pass passes passed fail fails failed test tests".split()
)
MAX_SUGGESTIONS = 20


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2 and t not in _STOPWORDS}


def create_from_template(
    db: sqlite3.Connection, project_id: int, key: str, values: dict, created_by: str = "", **owner
) -> int:
    try:
        rendered = templates.render(key, values)
    except templates.TemplateError as exc:
        raise AcceptanceError(str(exc)) from exc
    return models.create(
        db, project_id, rendered["title"], rendered["description"], origin="TEMPLATE",
        created_by=created_by, template_key=key, hints=rendered["hints"], **owner,
    )


def sync_from_work_items(db: sqlite3.Connection, sprint_id: int, approved_by: str) -> int:
    """When a Sprint is approved its planned tasks' acceptance text becomes
    first-class criteria. A person just reviewed each one (readiness requires
    it), so they start APPROVED, attributed to the Sprint approver.

    Also promotes any ACCEPTED Backlog "Design this item" test proposal
    (§51, `app.backlog.design`) belonging to a backlog item this work item
    covers -- the point a proposal stops being advisory-only and becomes a
    real, gating criterion, the same "person accepts findings before they
    change acceptance criteria" rule Research already follows (§50)."""
    from app.backlog import persistence as backlog
    from app.sprints import persistence as sprints

    sprint = sprints.get_sprint(db, sprint_id)
    created = 0
    for work in sprints.list_work_items(db, sprint_id):
        if work.status == "REJECTED":
            continue
        existing = {c.title for c in models.list_criteria(db, sprint.project_id, work_item_id=work.id)}
        for text in work.acceptance:
            if text not in existing:
                models.create(
                    db, sprint.project_id, text, work_item_id=work.id, origin="PLANNING",
                    created_by="planning", status="APPROVED", approved_by=approved_by,
                )
                existing.add(text)
                created += 1
        for backlog_item_id in work.backlog_item_ids:
            for proposal in backlog.list_test_proposals(db, backlog_item_id):
                if proposal.status != "ACCEPTED" or proposal.title in existing:
                    continue
                models.create(
                    db, sprint.project_id, proposal.title, proposal.description, work_item_id=work.id,
                    origin="AGENT", created_by="design", status="APPROVED", approved_by=approved_by,
                    pytest_node_id=proposal.pytest_node_id,
                )
                existing.add(proposal.title)
                created += 1
    return created


# -- workflow -------------------------------------------------------------------------------


def _require(db: sqlite3.Connection, criterion_id: int) -> Criterion:
    c = models.get(db, criterion_id)
    if c is None:
        raise LookupError(f"Criterion {criterion_id} not found")
    return c


def approve(db: sqlite3.Connection, criterion_id: int, by: str) -> None:
    """Review sign-off. The reviewer must not be whoever wrote the criterion:
    there are no user roles yet, so this is a name comparison (a soft control)."""
    c = _require(db, criterion_id)
    by = by.strip()
    if not by:
        raise AcceptanceError("Enter your name to approve")
    if c.created_by and by.lower() == c.created_by.lower():
        raise AcceptanceError("A criterion must be approved by someone other than its author")
    models.set_status(db, criterion_id, "APPROVED", approved_by=by, approved_at=models.now(),
                      verified_by=None, verified_at=None)


def waive(db: sqlite3.Connection, criterion_id: int, by: str, reason: str) -> None:
    _require(db, criterion_id)
    if not by.strip() or not reason.strip():
        raise AcceptanceError("A waiver needs your name and a reason")
    models.set_status(db, criterion_id, "WAIVED", waived_reason=reason.strip(),
                      verified_by=by.strip(), verified_at=models.now())


def verify(db: sqlite3.Connection, criterion_id: int, by: str, note: str = "") -> None:
    """Mark a criterion met. It must be approved first and have linked evidence.
    Independence is enforced at approval (author != approver); verification is
    evidence-based, so the same person may verify what they approved."""
    c = _require(db, criterion_id)
    by = by.strip()
    if not by:
        raise AcceptanceError("Enter your name to verify")
    if c.status != "APPROVED":
        raise AcceptanceError("Approve the criterion before verifying it")
    evidence_id = models.add_evidence(db, criterion_id, "MANUAL", None, note, recorded_by=by) if note.strip() else None
    if not models.list_evidence(db, criterion_id, "LINKED"):
        raise AcceptanceError("Link at least one piece of evidence (or add a manual note) before verifying")
    models.set_status(db, criterion_id, "VERIFIED", evidence_id=evidence_id, verified_by=by, verified_at=models.now())


def fail(db: sqlite3.Connection, criterion_id: int, by: str, note: str = "") -> None:
    _require(db, criterion_id)
    if not by.strip():
        raise AcceptanceError("Enter your name")
    evidence_id = None
    if note.strip():
        evidence_id = models.add_evidence(db, criterion_id, "MANUAL", None, f"FAILED: {note.strip()}", state="LINKED", recorded_by=by)
    models.set_status(db, criterion_id, "FAILED", evidence_id=evidence_id, verified_by=by.strip(), verified_at=models.now())


def reopen(db: sqlite3.Connection, criterion_id: int, by: str) -> None:
    _require(db, criterion_id)
    if not by.strip():
        raise AcceptanceError("Enter your name")
    models.set_status(db, criterion_id, "APPROVED", verified_by=None, verified_at=None, waived_reason="")


# -- evidence ---------------------------------------------------------------------------------


def _scope_executions(db: sqlite3.Connection, criterion: Criterion) -> list[int]:
    """Pipeline executions whose output can prove this criterion: the
    verification runs of the Ralph run(s) it belongs to, or -- for a Sprint
    QA criterion, which has neither -- every pipeline execution scoped to
    that Sprint (§51; `pipeline_executions.sprint_id`, set by
    `app.sprints.qa.run_suite`)."""
    if criterion.sprint_id:
        return [
            r[0] for r in db.execute("SELECT id FROM pipeline_executions WHERE sprint_id = ?", (criterion.sprint_id,))
        ]
    if criterion.ralph_run_id:
        run_ids = [criterion.ralph_run_id]
    else:
        run_ids = [
            r[0] for r in db.execute("SELECT id FROM ralph_runs WHERE work_item_id = ?", (criterion.work_item_id,))
        ]
    if not run_ids:
        return []
    marks = ",".join("?" * len(run_ids))
    return [
        r[0]
        for r in db.execute(
            f"SELECT DISTINCT verification_execution_id FROM ralph_iterations "
            f"WHERE run_id IN ({marks}) AND verification_execution_id IS NOT NULL",
            run_ids,
        )
    ]


def suggest_evidence(db: sqlite3.Connection, criterion_id: int) -> list[int]:
    """Find likely evidence and record it as SUGGESTED for a person to accept.

    A passed step or an artifact matches when its name/step/tags share a
    keyword with the criterion title, or when it is the kind the criterion's
    template says usually proves it. Only evidence from this criterion's own
    executions is considered, and already-linked/dismissed evidence is left."""
    c = _require(db, criterion_id)
    executions = _scope_executions(db, c)
    if not executions:
        return []
    words = _tokens(c.title + " " + c.description)
    step_types = set(c.hints.get("step_types", []))
    kinds = set(c.hints.get("artifact_kinds", []))
    marks = ",".join("?" * len(executions))
    made: list[int] = []

    for row in db.execute(
        f"SELECT id, element_name, element_type FROM step_executions WHERE execution_id IN ({marks}) "
        "AND status = 'PASSED' ORDER BY id DESC",
        executions,
    ):
        keyword = words & _tokens(row["element_name"].replace("_", " "))
        if keyword or row["element_type"] in step_types:
            made.append(_suggest(db, c.id, "TEST_RESULT", row["id"],
                                 f"Passed step '{row['element_name']}'" + (f" (matches: {', '.join(sorted(keyword))})" if keyword else " (expected step type)")))

    artifacts, _ = artifact_models.search(db, c.project_id, limit=500)
    for a in artifacts:
        if a.execution_id not in executions:
            continue
        keyword = words & (_tokens(a.name.replace("_", " ")) | _tokens(a.step_name.replace("_", " ")) | {t for t in a.tags})
        if keyword or (a.kind in kinds and a.kind != "log"):
            made.append(_suggest(db, c.id, "ARTIFACT", a.id,
                                 f"{a.kind} '{a.name}'" + (f" (matches: {', '.join(sorted(keyword))})" if keyword else " (expected artifact kind)")))
    return [m for m in made if m is not None][:MAX_SUGGESTIONS]


def _suggest(db, criterion_id: int, evidence_type: str, reference_id: int, note: str) -> int | None:
    existing = db.execute(
        "SELECT id FROM acceptance_evidence WHERE criterion_id = ? AND evidence_type = ? AND reference_id = ?",
        (criterion_id, evidence_type, reference_id),
    ).fetchone()
    if existing:
        return None  # linked, dismissed or already suggested: do not resurface
    return models.add_evidence(db, criterion_id, evidence_type, reference_id, note, state="SUGGESTED")


def suggest_for_run(db: sqlite3.Connection, ralph_run_id: int) -> int:
    """Refresh suggestions for every open criterion of a Ralph run (its own and
    its planned task's). Called by Ralph after verification passes."""
    run = db.execute("SELECT project_id, work_item_id FROM ralph_runs WHERE id = ?", (ralph_run_id,)).fetchone()
    count = 0
    for c in run_criteria(db, ralph_run_id):
        if c.status in ("DRAFT", "APPROVED", "FAILED"):
            count += len(suggest_evidence(db, c.id))
    return count


def sync_qa_results(db: sqlite3.Connection, project_id: int, sprint_id: int, execution_id: int) -> int:
    """Resolve a Sprint QA suite run (§51, `app.sprints.qa.run_suite`) against
    its criteria. Each included criterion has a `pytest_node_id` and ran as
    one TEST element in `execution_id`, named `f"qa-{criterion.id}"` -- an
    exact match, unlike the keyword-guessing `suggest_evidence` above, so the
    result goes straight to LINKED evidence and VERIFIED/FAILED, not
    SUGGESTED. Returns the number of criteria updated; already-synced steps
    (checked by an existing TEST_RESULT evidence row for that step) are left
    alone so re-rendering the QA screen never double-applies a result."""
    updated = 0
    for c in models.list_criteria(db, project_id, sprint_id=sprint_id):
        if not c.pytest_node_id or c.status not in ("DRAFT", "APPROVED", "FAILED"):
            continue
        step = executions.latest_step(db, execution_id, f"qa-{c.id}")
        if step is None or step.status not in ("PASSED", "FAILED"):
            continue
        already = db.execute(
            "SELECT 1 FROM acceptance_evidence WHERE criterion_id = ? AND evidence_type = 'TEST_RESULT' "
            "AND reference_id = ?",
            (c.id, step.id),
        ).fetchone()
        if already:
            continue
        models.add_evidence(
            db, c.id, "TEST_RESULT", step.id,
            note=f"pytest {c.pytest_node_id}", state="LINKED", recorded_by="system:sprint-qa",
        )
        target = "VERIFIED" if step.status == "PASSED" else "FAILED"
        status = c.status
        if status in ("FAILED", "VERIFIED") and target not in models.TRANSITIONS.get(status, ()):
            # Neither FAILED nor VERIFIED transitions directly to the other
            # (only a person re-opening one does, via reopen()) -- a QA
            # re-run flipping a check's result re-opens it first, the same
            # ... -> APPROVED step reopen() performs, before applying the new
            # result, so a fix (or a regression) shows up on the next run
            # without needing a manual re-approve in between.
            models.set_status(db, c.id, "APPROVED", verified_by=None, verified_at=None, waived_reason="")
            status = "APPROVED"
        if target in models.TRANSITIONS.get(status, ()):
            models.set_status(
                db, c.id, target,
                verified_by="system:sprint-qa" if target == "VERIFIED" else None,
                verified_at=models.now() if target == "VERIFIED" else None,
            )
        updated += 1
    return updated


def accept_evidence(db: sqlite3.Connection, evidence_id: int, by: str) -> None:
    ev = models.get_evidence(db, evidence_id)
    if ev is None:
        raise LookupError("Evidence not found")
    if not by.strip():
        raise AcceptanceError("Enter your name to link evidence")
    models.set_evidence_state(db, evidence_id, "LINKED", by)


def dismiss_evidence(db: sqlite3.Connection, evidence_id: int, by: str) -> None:
    if models.get_evidence(db, evidence_id) is None:
        raise LookupError("Evidence not found")
    models.set_evidence_state(db, evidence_id, "DISMISSED", by)


def link_evidence(db: sqlite3.Connection, criterion_id: int, evidence_type: str, reference_id: int | None,
                  note: str, by: str) -> int:
    c = _require(db, criterion_id)
    if not by.strip():
        raise AcceptanceError("Enter your name to link evidence")
    if evidence_type == "ARTIFACT":
        artifact = artifact_models.get_artifact(db, reference_id or 0)
        if artifact is None or artifact.project_id != c.project_id:
            raise AcceptanceError("That artifact is not in this project")
    elif evidence_type == "TEST_RESULT":
        step = db.execute(
            "SELECT e.project_id FROM step_executions s JOIN pipeline_executions e ON e.id = s.execution_id "
            "WHERE s.id = ?", (reference_id,),
        ).fetchone()
        if step is None or step["project_id"] != c.project_id:
            raise AcceptanceError("That step result is not in this project")
    return models.add_evidence(db, criterion_id, evidence_type, reference_id, note, recorded_by=by)


# -- completion gate -----------------------------------------------------------------------------


def run_criteria(db: sqlite3.Connection, ralph_run_id: int) -> list[Criterion]:
    run = db.execute("SELECT project_id, work_item_id FROM ralph_runs WHERE id = ?", (ralph_run_id,)).fetchone()
    if run is None:
        return []
    found = models.list_criteria(db, run["project_id"], ralph_run_id=ralph_run_id)
    if run["work_item_id"]:
        found += models.list_criteria(db, run["project_id"], work_item_id=run["work_item_id"])
    return found


def completion_blockers(db: sqlite3.Connection, ralph_run_id: int) -> list[Criterion]:
    """Required criteria that are not verified or waived. Unreviewed (DRAFT)
    ones count: the default policy is that newly added criteria block
    completion until someone has looked at them (RUN_AND_RALPH §15)."""
    return [c for c in run_criteria(db, ralph_run_id) if c.required and c.status not in models.SATISFIED]


def task_status(db: sqlite3.Connection, work_item_id: int, project_id: int) -> dict:
    criteria = models.list_criteria(db, project_id, work_item_id=work_item_id)
    blockers = [c for c in criteria if c.required and c.status not in models.SATISFIED]
    return {"total": len(criteria), "satisfied": sum(c.status in models.SATISFIED for c in criteria),
            "blocking": [c.id for c in blockers], "complete": bool(criteria) and not blockers}
