"""Acceptance criteria templates (docs/SPRINT_PLANNING_AND_BACKLOG.md §21).

`title`/`description` use `{field}` placeholders. `hints` tell evidence
suggestion what kind of step or artifact usually proves the criterion.
"""
from __future__ import annotations

TEMPLATES: dict[str, dict] = {
    "api-200": {
        "name": "API endpoint returns 200 with correct schema",
        "title": "{endpoint} returns 200 with the documented schema",
        "description": "A request to {endpoint} succeeds and the response body matches the schema.",
        "fields": ["endpoint"],
        "hints": {"step_types": ["TEST", "HTTP_REQUEST"], "artifact_kinds": ["report"]},
    },
    "unit-tests-green": {
        "name": "All unit tests pass",
        "title": "All unit tests pass",
        "description": "The full unit test suite finishes with zero failures.",
        "fields": [],
        "hints": {"step_types": ["TEST"], "artifact_kinds": ["report", "log"]},
    },
    "integration-real-db": {
        "name": "Integration test with a real database succeeds",
        "title": "Integration tests pass against a real database",
        "description": "Integration tests run against a real (not mocked) database and pass.",
        "fields": [],
        "hints": {"step_types": ["TEST"], "artifact_kinds": ["report", "log"]},
    },
    "screenshot-matches-design": {
        "name": "Screenshot matches design mock",
        "title": "{screen} screenshot matches the design mock",
        "description": "A screenshot of {screen} at 390px and desktop width matches the approved mock.",
        "fields": ["screen"],
        "hints": {"step_types": ["PLAYWRIGHT", "SCREENSHOT"], "artifact_kinds": ["screenshot"]},
    },
    "performance-threshold": {
        "name": "Performance metric under threshold",
        "title": "{metric} is under {threshold}ms",
        "description": "{metric} stays under {threshold}ms in the performance run.",
        "fields": ["metric", "threshold"],
        "hints": {"step_types": ["TEST", "COMMAND"], "artifact_kinds": ["report"]},
    },
    "security-scan-clean": {
        "name": "Security scan finds no critical issues",
        "title": "Security scan reports no critical issues",
        "description": "The security scan completes and reports no critical or high findings.",
        "fields": [],
        "hints": {"step_types": ["COMMAND", "TEST"], "artifact_kinds": ["report"]},
    },
    # Sprint QA checklist defaults (docs/SPRINT_PLANNING_AND_BACKLOG.md §51):
    # `default_for_sprint` entries pre-populate every sprint's QA checklist
    # (app/sprints/qa.py's `default_checklist`). `pytest_node_id` starts empty
    # -- unlike the other templates above, these are seeded once per project
    # against that project's own test suite, not a fixed path this shared
    # template dict could ever know; the QA screen lets it be filled in (or
    # left blank, in which case the checklist item stays a manual/agent-judged
    # check rather than a framework-run one) per project.
    "qa-login-regression": {
        "name": "Login regression",
        "title": "Login still works",
        "description": "A user can log in with valid credentials and reaches the signed-in view.",
        "fields": [],
        "hints": {"step_types": ["TEST"], "artifact_kinds": ["report"]},
        "default_for_sprint": True,
        "pytest_node_id": "",
    },
    "qa-nav-click-smoke": {
        "name": "Navigation click smoke test",
        "title": "Primary navigation links all resolve",
        "description": "Every primary navigation link loads without error.",
        "fields": [],
        "hints": {"step_types": ["TEST", "PLAYWRIGHT"], "artifact_kinds": ["report"]},
        "default_for_sprint": True,
        "pytest_node_id": "",
    },
    "qa-code-standards": {
        "name": "Code standards / lint",
        "title": "Code standards checks pass",
        "description": "The project's lint/formatting/static-analysis check reports no violations.",
        "fields": [],
        "hints": {"step_types": ["TEST", "COMMAND"], "artifact_kinds": ["report"]},
        "default_for_sprint": True,
        "pytest_node_id": "",
    },
    "migration-safety-clean": {
        "name": "Database migrations are safe",
        "title": "Database migrations are safe",
        "description": "Any new or changed database migrations pass the project's migration linter/checker with no errors.",
        "fields": [],
        "hints": {"step_types": ["TEST", "COMMAND"], "artifact_kinds": ["report", "log"]},
    },
    "dependency-audit-clean": {
        "name": "No vulnerable new dependencies",
        "title": "New dependencies have no known high/critical vulnerabilities",
        "description": "A dependency audit of packages added or upgraded by this change reports no high or critical severity vulnerabilities.",
        "fields": [],
        "hints": {"step_types": ["TEST", "COMMAND"], "artifact_kinds": ["report"]},
    },
    "no-n-plus-one-queries": {
        "name": "No N+1 query regressions",
        "title": "No N+1 query regressions introduced",
        "description": "The changed code paths show no new N+1 query patterns compared to the pre-change baseline.",
        "fields": [],
        "hints": {"step_types": ["TEST", "COMMAND"], "artifact_kinds": ["report", "log"]},
    },
    "new-public-api-documented": {
        "name": "New public API is documented",
        "title": "New public functions/methods are documented",
        "description": "Every new public function, method, or endpoint introduced by this change has a docstring or equivalent description.",
        "fields": [],
        "hints": {"step_types": ["TEST", "COMMAND"], "artifact_kinds": ["report"]},
    },
}

DEFAULT_SPRINT_TEMPLATES = tuple(key for key, t in TEMPLATES.items() if t.get("default_for_sprint"))


class TemplateError(ValueError):
    """Unknown template or missing/invalid field values."""


def list_templates() -> list[dict]:
    return [{"key": k, **v} for k, v in TEMPLATES.items()]


def render(key: str, values: dict | None = None) -> dict:
    """Fill a template. Every declared field is required, so a criterion never
    carries a literal `{placeholder}`."""
    template = TEMPLATES.get(key)
    if template is None:
        raise TemplateError(f"Unknown template {key!r}")
    values = {k: str(v).strip() for k, v in (values or {}).items()}
    missing = [f for f in template["fields"] if not values.get(f)]
    if missing:
        raise TemplateError("Fill in: " + ", ".join(missing))
    fill = {f: values[f] for f in template["fields"]}
    return {
        "title": template["title"].format(**fill),
        "description": template["description"].format(**fill),
        "hints": template["hints"],
    }
