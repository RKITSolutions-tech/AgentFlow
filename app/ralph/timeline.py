"""Ralph iteration views over the run's verification executions (docs/PIPELINE_VISUALISATION.md §19).

Every iteration verifies through its own pipeline execution, so a run has no
single event stream. `merged_timeline` stitches the executions' recorded events
into one ordered list and one swimlane per iteration; `compare` sets two
iterations side by side. Read-only: nothing is re-executed."""
from __future__ import annotations

import difflib
import sqlite3
from pathlib import Path

from app.acceptance import models as acceptance_models
from app.acceptance import service as acceptance_service
from app.artifacts import models as artifact_models
from app.pipelines import executions, replay
from app.ralph import models
from app.runs import artifacts as run_artifacts

MAX_DIFF_LINES = 2000
MAX_OUTPUT_SIZE = 100_000  # 100KB limit for test output display


def merged_timeline(db: sqlite3.Connection, run_id: int, include_hidden: bool = False) -> dict:
    """One lane per iteration plus every event in time order.

    Lane events keep their 1-based `position` in the iteration's own execution,
    which is what `#replay=N` on that execution's page opens at."""
    lanes, merged = [], []
    for it in models.list_iterations(db, run_id):
        events = []
        if it.verification_execution_id:
            execution = executions.get_execution(db, it.verification_execution_id)
            if execution is not None:
                for ev in replay.Replayer(db, execution).events:
                    if ev.visible or include_hidden:
                        events.append({**ev.as_dict(), "iteration": it.number, "execution_id": execution.id})
        lanes.append({
            "number": it.number, "status": it.status, "execution_id": it.verification_execution_id,
            "started_at": it.started_at, "completed_at": it.completed_at, "events": events,
            "files_changed": len(it.changed_files), "next_action": it.next_action,
        })
        merged.extend(events)
    merged.sort(key=lambda e: (e["at"], e["iteration"], e["position"]))
    return {"run_id": run_id, "lanes": lanes, "events": merged}


def _diff(a: str, b: str) -> list[dict]:
    """Line diff as [{kind: add|del|ctx|gap, text}]; long texts are cut off."""
    out = []
    for line in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=2):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            out.append({"kind": "gap", "text": "…"})
        elif line.startswith("+"):
            out.append({"kind": "add", "text": line[1:]})
        elif line.startswith("-"):
            out.append({"kind": "del", "text": line[1:]})
        else:
            out.append({"kind": "ctx", "text": line[1:]})
        if len(out) >= MAX_DIFF_LINES:
            out.append({"kind": "gap", "text": "(diff truncated)"})
            break
    return out


def _read_test_output(root: str, raw_data_reference: str | None) -> str:
    """Read test output from artifact file, limiting to MAX_OUTPUT_SIZE."""
    if not raw_data_reference:
        return ""
    try:
        path = run_artifacts._resolve_within(root, raw_data_reference)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(MAX_OUTPUT_SIZE)
        return content
    except Exception:
        return ""


def _screenshot(db: sqlite3.Connection, project_id: int | None, execution_id: int, step_name: str):
    """Most recent screenshot artifact indexed for this step, if any (§ Artifact Library)."""
    if not project_id:
        return None
    shots, _ = artifact_models.search(
        db, project_id, kinds=["screenshot"], execution_id=execution_id, step_name=step_name, limit=1,
    )
    return shots[0] if shots else None


def _side(db: sqlite3.Connection, it: models.Iteration, root: str = "", project_id: int | None = None) -> dict:
    steps: dict[str, dict] = {}  # name -> {"status": str, "output": str, "screenshot": Artifact | None}
    if it.verification_execution_id:
        for s in executions.list_steps(db, it.verification_execution_id):
            output = _read_test_output(root, s.raw_data_reference)
            screenshot = _screenshot(db, project_id, it.verification_execution_id, s.element_name)
            steps[s.element_name] = {"status": s.status, "output": output, "screenshot": screenshot}  # last attempt wins
    return {
        "number": it.number, "status": it.status, "prompt": it.prompt, "reply": it.reply,
        "analysis": it.analysis, "changed_files": it.changed_files, "commit": it.commit_sha,
        "execution_id": it.verification_execution_id, "steps": steps, "next_action": it.next_action,
    }


def _criteria_progress(db: sqlite3.Connection, run_id: int, a: int, b: int) -> list[dict]:
    """Every acceptance criterion attached to this run (or its planned task), with
    its current status and whichever transitions its history recorded between
    iterations `a` and `b` (§ Sprint Planning & Backlog #49)."""
    lo, hi = min(a, b), max(a, b)
    out = []
    for c in acceptance_service.run_criteria(db, run_id):
        history = [
            h for h in acceptance_models.list_history(db, c.id)
            if h.iteration_number is not None and lo <= h.iteration_number <= hi
        ]
        out.append({"id": c.id, "title": c.title, "required": c.required, "status": c.status, "history": history})
    return out


def compare(db: sqlite3.Connection, run_id: int, a: int, b: int, root: str = "", project_id: int | None = None) -> dict | None:
    """Iteration numbers `a` and `b` of a run side by side: prompt and reply
    (with line diffs), analysis, changed files, verification step results, test output
    diffs, screenshots (when `project_id` is given so the Artifact Library can be queried),
    and acceptance criteria status progression between the two iterations."""
    by_number = {i.number: i for i in models.list_iterations(db, run_id)}
    if a not in by_number or b not in by_number:
        return None
    left, right = _side(db, by_number[a], root, project_id), _side(db, by_number[b], root, project_id)
    names = list(dict.fromkeys([*left["steps"], *right["steps"]]))
    fa, fb = set(left["changed_files"]), set(right["changed_files"])

    # Build step comparison with status and test output diffs
    steps = []
    for n in names:
        left_step = left["steps"].get(n, {"status": "—", "output": ""})
        right_step = right["steps"].get(n, {"status": "—", "output": ""})
        output_diff = _diff(
            left_step.get("output", ""),
            right_step.get("output", "")
        ) if (left_step.get("output") or right_step.get("output")) else []

        steps.append({
            "name": n,
            "a": left_step["status"] if isinstance(left_step, dict) else left_step,
            "b": right_step["status"] if isinstance(right_step, dict) else right_step,
            "changed": (left_step.get("status") if isinstance(left_step, dict) else left_step) !=
                      (right_step.get("status") if isinstance(right_step, dict) else right_step),
            "output_diff": output_diff,
        })

    screenshots = [
        {"name": n, "a": left["steps"].get(n, {}).get("screenshot"), "b": right["steps"].get(n, {}).get("screenshot")}
        for n in names
        if left["steps"].get(n, {}).get("screenshot") or right["steps"].get(n, {}).get("screenshot")
    ]

    return {
        "a": left, "b": right,
        "prompt_diff": _diff(left["prompt"], right["prompt"]),
        "reply_diff": _diff(left["reply"], right["reply"]),
        "files": {"only_a": sorted(fa - fb), "only_b": sorted(fb - fa), "both": sorted(fa & fb)},
        "steps": steps,
        "screenshots": screenshots,
        "acceptance_criteria": _criteria_progress(db, run_id, a, b),
    }
