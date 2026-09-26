"""Ralph iteration views over the run's verification executions (docs/PIPELINE_VISUALISATION.md §19).

Every iteration verifies through its own pipeline execution, so a run has no
single event stream. `merged_timeline` stitches the executions' recorded events
into one ordered list and one swimlane per iteration; `compare` sets two
iterations side by side. Read-only: nothing is re-executed."""
from __future__ import annotations

import difflib
import sqlite3

from app.pipelines import executions, replay
from app.ralph import models

MAX_DIFF_LINES = 2000


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


def _side(db: sqlite3.Connection, it: models.Iteration) -> dict:
    steps: dict[str, str] = {}
    if it.verification_execution_id:
        for s in executions.list_steps(db, it.verification_execution_id):
            steps[s.element_name] = s.status  # the last attempt wins
    return {
        "number": it.number, "status": it.status, "prompt": it.prompt, "reply": it.reply,
        "analysis": it.analysis, "changed_files": it.changed_files, "commit": it.commit_sha,
        "execution_id": it.verification_execution_id, "steps": steps, "next_action": it.next_action,
    }


def compare(db: sqlite3.Connection, run_id: int, a: int, b: int) -> dict | None:
    """Iteration numbers `a` and `b` of a run side by side: prompt and reply
    (with line diffs), analysis, changed files, and verification step results."""
    by_number = {i.number: i for i in models.list_iterations(db, run_id)}
    if a not in by_number or b not in by_number:
        return None
    left, right = _side(db, by_number[a]), _side(db, by_number[b])
    names = list(dict.fromkeys([*left["steps"], *right["steps"]]))
    fa, fb = set(left["changed_files"]), set(right["changed_files"])
    return {
        "a": left, "b": right,
        "prompt_diff": _diff(left["prompt"], right["prompt"]),
        "reply_diff": _diff(left["reply"], right["reply"]),
        "files": {"only_a": sorted(fa - fb), "only_b": sorted(fb - fa), "both": sorted(fa & fb)},
        "steps": [
            {"name": n, "a": left["steps"].get(n, "—"), "b": right["steps"].get(n, "—"),
             "changed": left["steps"].get(n) != right["steps"].get(n)}
            for n in names
        ],
    }
