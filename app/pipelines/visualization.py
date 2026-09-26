"""Turns a stored execution into graph JSON for the pipeline view
(docs/PIPELINE_VISUALISATION.md). Pure data: layout orientation is a CSS
concern (§4), so this only assigns each node a layer (its column on desktop,
its row on mobile) and lists the edges."""
from __future__ import annotations

import sqlite3

from app.pipelines import executions, schema
from app.pipelines.executions import Execution, StepExecution
from app.runs.models import duration_seconds
from app.runs.security import redact

# state -> (label, icon). Text and icon always accompany colour (§6).
STATES: dict[str, tuple[str, str]] = {
    "PENDING": ("Pending", "○"),
    "RUNNING": ("Running", "▶"),
    "RETRYING": ("Retrying", "↻"),
    "PASSED": ("Passed", "✓"),
    "FAILED": ("Failed", "✕"),
    "WARNING": ("Warning", "!"),
    "WAITING": ("Waiting for human", "⏸"),
    "SKIPPED": ("Skipped", "⤼"),
    "DISABLED": ("Disabled", "⊘"),
    "CANCELLED": ("Cancelled", "■"),
    "TIMED_OUT": ("Timed out", "⏱"),
}
SUMMARY_CHARS = 140


def node_state(element: dict, steps: list[StepExecution]) -> str:
    if not steps:
        return "DISABLED" if element.get("enabled", "ENABLED") == "DISABLED" else "PENDING"
    last = steps[-1]
    if last.status == "RUNNING":
        return "RETRYING" if last.attempt > 1 else "RUNNING"
    if last.status == "FAILED" and (element.get("compensation") or {}).get("action") == "CONTINUE":
        return "WARNING"
    return last.status


def build_graph(
    db: sqlite3.Connection, execution: Execution, steps: list[StepExecution] | None = None
) -> dict:
    """`steps` replaces the stored step rows: historical replay passes the steps
    as they were at an earlier event (pipelines/replay.py)."""
    elements = execution.resolved_configuration["elements"]
    by_element: dict[str, list[StepExecution]] = {}
    for step in (steps if steps is not None else executions.list_steps(db, execution.id)):
        by_element.setdefault(step.element_name, []).append(step)

    layer_of: dict[str, int] = {}
    for el in elements:  # composed order is topological
        deps = [d for d in el.get("depends_on") or [] if d in layer_of]
        layer_of[el["name"]] = 1 + max((layer_of[d] for d in deps), default=-1)
    # Teardown runs after everything else, in its own trailing layers.
    main_depth = max((layer_of[e["name"]] for e in elements if e.get("phase") != "TEARDOWN"), default=-1)
    for el in elements:
        if el.get("phase") == "TEARDOWN":
            layer_of[el["name"]] += main_depth + 1

    rows: dict[int, int] = {}
    nodes, edges = [], []
    for el in elements:
        name = el["name"]
        steps = by_element.get(name, [])
        state = node_state(el, steps)
        last = steps[-1] if steps else None
        layer = layer_of[name]
        row = rows[layer] = rows.get(layer, -1) + 1
        duration = duration_seconds(last.started_at, last.completed_at) if last and last.started_at else None
        label, icon = STATES[state]
        nodes.append(
            {
                "id": name,
                "label": name.split(".")[-1],
                "group": name.rsplit(".", 1)[0] if "." in name else "",
                "type": el["type"],
                "category": schema.category(el["type"]),
                "phase": el.get("phase", "MAIN"),
                "state": state,
                "state_label": label,
                "icon": icon,
                "attempt": last.attempt if last else 0,
                "attempts": len(steps),
                "duration": duration,
                "summary": ((last.error_summary or last.result_summary) if last else "")[:SUMMARY_CHARS],
                "step_id": last.id if last else None,
                "layer": layer,
                "row": row,
                "current": execution.status == "RUNNING" and state in ("RUNNING", "RETRYING"),
            }
        )
        for dep in el.get("depends_on") or []:
            edges.append({"from": dep, "to": name, "kind": "dependency"})
        comp = el.get("compensation") or {}
        action = comp.get("action", "STOP")
        if action in ("LOOP", "RUN_STEP") and comp.get("step"):
            label_text = f"loop (max {comp.get('max_loops')})" if action == "LOOP" else "on failure"
            edges.append({"from": name, "to": comp["step"], "kind": "compensation", "action": action, "label": label_text})
        elif action == "START_PIPELINE":
            nodes[-1]["compensation_note"] = f"on failure: start pipeline {comp.get('pipeline')}"

    return {
        "execution": {
            "id": execution.id,
            "status": execution.status,
            "reason": execution.reason,
            "warnings": execution.warnings,
            "needs_attention": execution.needs_attention,
            "active": execution.status in ("PENDING", "RUNNING"),
            "waiting_step_id": execution.waiting_step_id,
            "loops": execution.loops,
        },
        "nodes": nodes,
        "edges": edges,
        "layers": (max(layer_of.values()) + 1) if layer_of else 0,
    }


def _clean(value, patterns):
    """Mask secrets inside element configuration before it reaches the browser."""
    if isinstance(value, str):
        return redact(value, patterns)[0]
    if isinstance(value, dict):
        return {k: _clean(v, patterns) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v, patterns) for v in value]
    return value


def step_detail(
    db: sqlite3.Connection, root: str, execution: Execution, name: str, patterns: tuple[str, ...] = (),
    steps: list[StepExecution] | None = None, events: list | None = None,
) -> dict | None:
    """Everything the inspector shows for one element: every attempt, the
    events that mention it, and a preview of its captured output. `steps` and
    `events` override the stored rows for historical replay."""
    import os

    from app.runs.artifacts import ArtifactPathError, _resolve_within

    elements = {e["name"]: e for e in execution.resolved_configuration["elements"]}
    if name not in elements:
        return None
    element = elements[name]
    all_steps = steps if steps is not None else executions.list_steps(db, execution.id)
    attempts = [s for s in all_steps if s.element_name == name]
    attempt_ids = {s.id for s in attempts}
    events = [
        {"type": e.event_type, "data": e.data, "at": e.created_at}
        for e in (events if events is not None else executions.list_events(db, execution.id))
        if e.step_execution_id in attempt_ids
    ]
    detail_attempts = []
    for s in attempts:
        raw = ""
        if s.raw_data_reference:
            try:
                with open(_resolve_within(root, s.raw_data_reference), "rb") as fh:
                    raw = fh.read(64 * 1024).decode("utf-8", "replace")
            except (OSError, ArtifactPathError):
                raw = "(output file missing)"
        detail_attempts.append(
            {
                "id": s.id, "attempt": s.attempt, "status": s.status, "started_at": s.started_at,
                "completed_at": s.completed_at, "exit_code": s.exit_code, "input": s.input_reference,
                "result": s.result_summary, "error": s.error_summary, "output": raw,
                "redacted": s.redacted, "session_id": s.session_id, "process_id": s.process_id,
                "log_url": bool(s.raw_data_reference),
                "duration": duration_seconds(s.started_at, s.completed_at) if s.started_at else None,
            }
        )
    state = node_state(element, attempts)
    return {
        "name": name,
        "type": element["type"],
        "category": schema.category(element["type"]),
        "phase": element.get("phase", "MAIN"),
        "state": state,
        "state_label": STATES[state][0],
        "configuration": _clean(element.get("config") or {}, patterns),
        "compensation": element.get("compensation") or {},
        "depends_on": element.get("depends_on") or [],
        "attempts": detail_attempts,
        "events": events,
        "waiting_step_id": next((s.id for s in attempts if s.status == "WAITING"), None),
    }
