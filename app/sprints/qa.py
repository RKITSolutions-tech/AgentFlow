"""Sprint QA checklist (docs/SPRINT_PLANNING_AND_BACKLOG.md §51).

A Sprint QA criterion has neither a `work_item_id` nor a `ralph_run_id` --
it belongs to the Sprint itself (`acceptance_criteria.sprint_id`). Default
checklist items come from `app.acceptance.templates.DEFAULT_SPRINT_TEMPLATES`
(login regression, nav-click smoke, code standards); unchecking one on the QA
screen does not delete it, it WAIVES it (`app.acceptance.service.waive`), so
a deliberate exclusion stays in the audit trail the same way any other
WAIVED criterion does.

Running the suite builds one TEST pipeline element per included (non-WAIVED,
`pytest_node_id` set) criterion -- `f"pytest {node_id}"`, named
`f"qa-{criterion.id}"` -- and executes them through the ordinary
`PipelineManager`/`PipelineEngine` machinery (project locking included) via
`app.pipelines.executions.create_execution` directly, bypassing
`PipelineEngine.create`'s stored-definition composition since the element
list is built fresh from the checklist every run, not authored ahead of
time. `pipeline_executions.pipeline_id` is NOT NULL, so the run still points
at a real `pipelines` row -- the `sprint-qa` builtin
(`app/pipelines/definitions/sprint-qa.json`), whose own placeholder element
never actually runs.

Blocking, not async (docs/AGENT_ADAPTER.md §25's same tradeoff for
Research/Design/Planning): the "Run QA suite" request waits for the suite via
`PipelineManager.join()` before returning, so results are ready to render in
the same response -- no separate polling/sync step for a person to remember.
"""
from __future__ import annotations

import sqlite3

from app.acceptance import models as acceptance_models
from app.acceptance import service as acceptance_service
from app.acceptance import templates as acceptance_templates
from app.pipelines import executions
from app.pipelines import persistence as pipeline_store
from app.pipelines import validator
from app.pipelines.manager import PipelineManager
from app.sprints.models import Sprint

PIPELINE_NAME = "sprint-qa"


class QAError(ValueError):
    """A Sprint QA action could not be completed."""


def default_checklist(db: sqlite3.Connection, project_id: int, sprint_id: int) -> int:
    """Idempotently seed this Sprint's QA checklist from every
    `default_for_sprint` template, skipping titles already present for this
    Sprint -- the same "existing titles" idempotency check
    `app.acceptance.service.sync_from_work_items` uses for planned-task
    acceptance text. Seeded APPROVED (not DRAFT): these are vetted, shared
    templates, not agent-drafted content needing an independent review pass
    before they can ever be verified."""
    existing = {c.title for c in acceptance_models.list_criteria(db, project_id, sprint_id=sprint_id)}
    created = 0
    for key in acceptance_templates.DEFAULT_SPRINT_TEMPLATES:
        template = acceptance_templates.TEMPLATES[key]
        if template["title"] in existing:
            continue
        acceptance_models.create(
            db, project_id, template["title"], template["description"], sprint_id=sprint_id,
            required=True, origin="TEMPLATE", template_key=key, hints=template["hints"],
            status="APPROVED", created_by="system", pytest_node_id=template.get("pytest_node_id", ""),
        )
        created += 1
    return created


def waive_check(db: sqlite3.Connection, criterion_id: int, by: str) -> None:
    """Uncheck: keeps the row, marked WAIVED (§51 -- audit trail, not deletion)."""
    acceptance_service.waive(db, criterion_id, by or "system", "Excluded from this Sprint's QA checklist")


def include_check(db: sqlite3.Connection, criterion_id: int, by: str) -> None:
    """Re-check a previously waived (or failed) item: the same re-open step a
    person does from the Acceptance detail page."""
    acceptance_service.reopen(db, criterion_id, by or "system")


def _pipeline(db: sqlite3.Connection):
    pipeline = pipeline_store.find_pipeline(db, PIPELINE_NAME)
    if pipeline is None:
        pipeline_store.seed_builtins(db)
        pipeline = pipeline_store.find_pipeline(db, PIPELINE_NAME)
    if pipeline is None:
        raise QAError(f"Built-in pipeline {PIPELINE_NAME!r} is not installed")
    return pipeline


def run_suite(
    db: sqlite3.Connection, manager: PipelineManager, project_id: int, sprint: Sprint, repository_id: int | None,
) -> int:
    """Build and run one TEST element per included checklist criterion, wait
    for the run to finish, then resolve results
    (`app.acceptance.service.sync_qa_results`). Returns the execution id."""
    checks = [
        c for c in acceptance_models.list_criteria(db, project_id, sprint_id=sprint.id)
        if c.status != "WAIVED" and c.pytest_node_id.strip()
    ]
    if not checks:
        raise QAError("No included QA check has a pytest node id set yet")

    elements, variables = [], {}
    for c in checks:
        var_name = f"node_id_{c.id}"
        elements.append({
            "name": f"qa-{c.id}",
            "type": "TEST",
            "depends_on": [],
            "config": {"command": f"pytest ${{vars.{var_name}}}"},
            # An omitted/empty compensation block defaults to action="STOP"
            # (engine.py), which would halt the whole suite at the first
            # failing check -- CONTINUE keeps every other check running.
            "compensation": {"action": "CONTINUE"},
        })
        variables[var_name] = c.pytest_node_id.strip()

    definition = {"name": PIPELINE_NAME, "type": "DEVELOPMENT", "elements": elements}
    errors = validator.validate(definition)
    if errors:
        raise QAError("; ".join(errors))

    pipeline = _pipeline(db)
    execution_id = executions.create_execution(
        db, pipeline.id, pipeline.current_version, project_id, elements,
        repository_id=repository_id, sprint_id=sprint.id, variables=variables,
    )
    manager.start(execution_id)
    manager.join(execution_id)
    acceptance_service.sync_qa_results(db, project_id, sprint.id, execution_id)
    return execution_id
