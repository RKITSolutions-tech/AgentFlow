"""Historical replay (docs/PIPELINE_VISUALISATION.md §17-18).

Replay never re-executes anything: it rebuilds what the graph and inspector
showed after the Nth recorded event by folding `pipeline_events` over the
stored `step_executions`. The events say *when* each step changed state; the
step rows supply the detail (output, exit code, timings) once a step has
finished as of that event.
"""
from __future__ import annotations

import bisect
import dataclasses
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass

from app.pipelines import executions, visualization
from app.pipelines.executions import Execution, ExecutionEvent, StepExecution

# event type -> (kind, icon, visible). Hidden events stay in the record and the
# export but the playhead skips them: they change no node.
EVENT_KINDS: dict[str, tuple[str, str, bool]] = {
    "PipelineStarted": ("milestone", "▶", True),
    "PipelineResumed": ("milestone", "▶", True),
    "PipelineCompleted": ("milestone", "✓", True),
    "PipelineFailed": ("milestone", "✕", True),
    "PipelineCancelled": ("milestone", "■", True),
    "StepStarted": ("step", "▶", True),
    "StepCompleted": ("step", "✓", True),
    "StepFailed": ("step", "✕", True),
    "StepSkipped": ("step", "⤼", True),
    "StepDisabled": ("step", "⊘", True),
    "ManualApprovalRequested": ("human", "⏸", True),
    "ManualApprovalReceived": ("human", "☑", True),
    "InterventionRequested": ("human", "⏸", True),
    "CompensationStarted": ("compensation", "↻", True),
    "CompensationCompleted": ("compensation", "↻", True),
    "LoopBack": ("compensation", "↺", True),
    "SubPipelineStarted": ("compensation", "⇲", True),
    "SubPipelineCompleted": ("compensation", "⇱", True),
    "ArtifactCollectionFailed": ("housekeeping", "!", False),
    "ResourceCleanup": ("housekeeping", "⌫", False),
}
_TERMINAL_STEP = ("PASSED", "FAILED", "SKIPPED", "DISABLED", "CANCELLED", "TIMED_OUT")
CHECKPOINT_EVERY = 500  # events between saved snapshots: a seek folds at most this many
MEMO_SIZE = 256  # reconstructed states kept for scrubbing back and forth
_PIPELINE_END = {"PipelineCompleted": "COMPLETED", "PipelineFailed": "FAILED", "PipelineCancelled": "CANCELLED"}


@dataclass
class ReplayEvent:
    position: int  # 1-based: "after event N"
    id: int
    type: str
    data: str
    at: str
    kind: str
    icon: str
    visible: bool
    node: str | None
    step_id: int | None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


class Replayer:
    """Rebuilds state for one execution. Load it once per request/session: the
    events and steps are read once and reconstructed states are memoised."""

    def __init__(self, db: sqlite3.Connection, execution: Execution):
        self.db = db
        self.execution = execution
        self._steps = {s.id: s for s in executions.list_steps(db, execution.id)}
        self._raw_events: list[ExecutionEvent] = executions.list_events(db, execution.id)
        self.events: list[ReplayEvent] = []
        for i, e in enumerate(self._raw_events, start=1):
            kind, icon, visible = EVENT_KINDS.get(e.event_type, ("other", "•", True))
            step = self._steps.get(e.step_execution_id) if e.step_execution_id else None
            self.events.append(ReplayEvent(
                i, e.id, e.event_type, e.data, e.created_at, kind, icon, visible,
                step.element_name if step else None, e.step_execution_id,
            ))
        self._memo: OrderedDict[int, dict] = OrderedDict()
        self._checkpoints: dict[int, tuple] = {}  # event count -> (status, waiting, loops, step_status)
        self._artifacts = [
            dict(r) for r in db.execute(
                "SELECT id, kind, name, step_execution_id, step_name, size FROM artifact_library "
                "WHERE execution_id = ? ORDER BY id", (execution.id,)
            )
        ]

    def __len__(self) -> int:
        return len(self.events)

    # -- reconstruction -------------------------------------------------------------

    def _clamp(self, n: int) -> int:
        return max(0, min(int(n), len(self.events)))

    def _fold(self, start: tuple, lo: int, hi: int) -> tuple:
        """Apply events `lo`..`hi` (0-based slice) to a copy of a (status, waiting, loops, steps) state."""
        status, waiting, loops, step_status = start[0], start[1], dict(start[2]), dict(start[3])
        for ev in self.events[lo:hi]:
            real = self._steps.get(ev.step_id) if ev.step_id else None
            t = ev.type
            if t == "StepStarted":
                step_status[ev.step_id] = "RUNNING"
            elif t in ("StepSkipped", "StepDisabled"):
                step_status[ev.step_id] = "SKIPPED" if t == "StepSkipped" else "DISABLED"
            elif t == "StepCompleted":
                step_status[ev.step_id] = "PASSED"
            elif t == "StepFailed":
                step_status[ev.step_id] = real.status if real and real.status in ("TIMED_OUT", "CANCELLED") else "FAILED"
            elif t == "ManualApprovalRequested":
                step_status[ev.step_id] = "WAITING"
                waiting, status = ev.step_id, "PAUSED"
            elif t == "ManualApprovalReceived":
                step_status[ev.step_id] = "PASSED" if ev.data.startswith("approved") else "FAILED"
            elif t == "LoopBack" and ev.node:
                loops[ev.node] = loops.get(ev.node, 0) + 1
            elif t in ("PipelineStarted", "PipelineResumed"):
                status, waiting = "RUNNING", None
            elif t in _PIPELINE_END:
                status, waiting = _PIPELINE_END[t], None
        return status, waiting, loops, step_status

    def _checkpoint_before(self, n: int) -> tuple[int, tuple]:
        """The nearest saved snapshot at or before event `n`. All checkpoints are
        built in one pass the first time they are needed, so every later seek
        folds at most CHECKPOINT_EVERY events however long the log is."""
        if not self._checkpoints:
            state = ("PENDING", None, {}, {})
            self._checkpoints[0] = state
            for lo in range(0, len(self.events), CHECKPOINT_EVERY):
                hi = min(lo + CHECKPOINT_EVERY, len(self.events))
                state = self._fold(state, lo, hi)
                if hi - lo == CHECKPOINT_EVERY:
                    self._checkpoints[hi] = state
        at = (n // CHECKPOINT_EVERY) * CHECKPOINT_EVERY
        while at not in self._checkpoints:
            at -= CHECKPOINT_EVERY
        return at, self._checkpoints[at]

    def state_at(self, n: int) -> dict:
        """Snapshot after the first `n` events: step statuses, execution status,
        waiting step, loop counters. Pure data, no database writes."""
        n = self._clamp(n)
        if n in self._memo:
            self._memo.move_to_end(n)
            return self._memo[n]
        at, saved = self._checkpoint_before(n)
        status, waiting, loops, step_status = self._fold(saved, at, n)
        state = {"event": n, "status": status, "waiting_step_id": waiting, "loops": loops, "steps": step_status}
        self._memo[n] = state
        if len(self._memo) > MEMO_SIZE:
            self._memo.popitem(last=False)
        return state

    def steps_at(self, n: int) -> list[StepExecution]:
        """Step rows as they stood: steps not yet started are absent; detail
        (output, exit code, completion time) appears only once terminal."""
        state = self.state_at(n)
        out = []
        for step_id, status in state["steps"].items():
            real = self._steps[step_id]
            if status in _TERMINAL_STEP or status == "WAITING":
                snap = dataclasses.replace(real, status=status)
                if status == "WAITING":
                    snap = dataclasses.replace(snap, completed_at=None)
            else:  # RUNNING: in flight, nothing produced yet
                snap = dataclasses.replace(
                    real, status=status, completed_at=None, exit_code=None, result_summary="",
                    error_summary="", raw_data_reference="", input_reference="",
                )
            out.append(snap)
        return sorted(out, key=lambda s: s.id)

    def execution_at(self, n: int) -> Execution:
        state = self.state_at(n)
        real = self.execution
        ended = state["status"] in ("COMPLETED", "FAILED", "CANCELLED")
        return dataclasses.replace(
            real, status=state["status"], waiting_step_id=state["waiting_step_id"],
            loops=state["loops"], reason=real.reason if state["event"] == len(self.events) else "",
            completed_at=real.completed_at if ended else None,
            needs_attention=real.needs_attention if state["event"] == len(self.events) else False,
            warnings=real.warnings if state["event"] == len(self.events) else 0,
        )

    def graph_at(self, n: int, expand: frozenset[str] = frozenset()) -> dict:
        graph = visualization.build_graph(self.db, self.execution_at(n), steps=self.steps_at(n), expand=expand)
        graph["replay"] = {"event": self._clamp(n), "total": len(self.events)}
        return graph

    def artifacts_at(self, n: int) -> list[dict]:
        """Artifacts registered by steps that had finished (or paused) by event n."""
        steps = {s.id for s in self.steps_at(n) if s.status in _TERMINAL_STEP or s.status == "WAITING"}
        return [a for a in self._artifacts if a["step_execution_id"] in steps]

    def inspector_at(self, n: int, node: str, root: str, patterns: tuple[str, ...] = ()) -> dict | None:
        n = self._clamp(n)
        raw = self._raw_events[:n]  # event `position` is its 1-based index
        return visualization.step_detail(
            self.db, root, self.execution_at(n), node, patterns, steps=self.steps_at(n), events=raw
        )

    def event_at(self, n: int) -> ReplayEvent | None:
        return self.events[n - 1] if 1 <= n <= len(self.events) else None

    def state_json(
        self, n: int, node: str | None = None, root: str = "", patterns: tuple[str, ...] = (),
        expand: frozenset[str] = frozenset(),
    ) -> dict:
        n = self._clamp(n)
        ev = self.event_at(n)
        node = node or (ev.node if ev else None)
        if node:  # inside a collapsed sub-pipeline the group node stands in for the step
            node = visualization.representative(node, expand)
        return {
            "event": n, "total": len(self.events), "current": ev.as_dict() if ev else None,
            "graph": self.graph_at(n, expand), "artifacts": self.artifacts_at(n),
            "node": node,
            "inspector": self.inspector_at(n, node, root, patterns) if node else None,
        }

    # -- timeline ---------------------------------------------------------------------

    def timeline(self, offset: int = 0, limit: int = 200, kind: str | None = None) -> dict:
        events = [e for e in self.events if not kind or e.kind == kind]
        page = events[max(offset, 0): max(offset, 0) + max(limit, 1)]
        return {
            "total": len(events), "offset": offset, "limit": limit,
            "kinds": sorted({e.kind for e in self.events}),
            "events": [e.as_dict() for e in page],
        }


class ReplayController:
    """Playhead over the *visible* events. Position 0 is "before anything ran".

    Stateless callers (the seek endpoint) build one at a known position, apply
    an action and read the new position; the browser drives playback by
    calling `tick` on a timer at `speed`."""

    SPEEDS = (0.5, 1.0, 2.0, 4.0)

    def __init__(self, replayer: Replayer, position: int = 0):
        self._replayer = replayer
        self._stops = [0] + [e.position for e in replayer.events if e.visible]
        self.position = 0
        self.playing = False
        self.speed = 1.0
        self.seek_to_event(position)

    @property
    def at_start(self) -> bool:
        return self.position == self._stops[0]

    @property
    def at_end(self) -> bool:
        return self.position == self._stops[-1]

    def seek_to_event(self, n: int) -> int:
        """Jump to event n, or the nearest visible stop at or before it."""
        self.position = self._stops[max(bisect.bisect_right(self._stops, n) - 1, 0)]
        return self.position

    def next(self) -> int:
        i = bisect.bisect_right(self._stops, self.position)
        if i < len(self._stops):
            self.position = self._stops[i]
        else:
            self.playing = False
        return self.position

    def previous(self) -> int:
        i = bisect.bisect_left(self._stops, self.position)
        if i > 0:
            self.position = self._stops[i - 1]
        return self.position

    def first(self) -> int:
        self.position = self._stops[0]
        return self.position

    def last(self) -> int:
        self.position = self._stops[-1]
        return self.position

    def play(self, speed: float = 1.0) -> None:
        if speed not in self.SPEEDS:
            raise ValueError(f"Speed must be one of {', '.join(str(s) for s in self.SPEEDS)}")
        if self.at_end:  # replaying a finished tape starts from the top
            self.first()
        self.speed, self.playing = speed, True

    def pause(self) -> None:
        self.playing = False

    def tick(self) -> int:
        """One playback step; stops itself at the end."""
        if self.playing:
            self.next()
        return self.position

    def interval_ms(self, base_ms: int = 1000) -> int:
        return int(base_ms / self.speed)

    def apply(self, action: str, event: int | None = None) -> int:
        actions = {"next": self.next, "previous": self.previous, "first": self.first, "last": self.last}
        if action == "seek":
            if event is None:
                raise ValueError("seek needs an event number")
            return self.seek_to_event(event)
        if action not in actions:
            raise ValueError(f"Unknown replay action {action!r}")
        return actions[action]()


def ralph_iterations(db: sqlite3.Connection, execution: Execution) -> dict | None:
    """If a Ralph iteration's verification created this execution, describe the
    whole run so the replay can hop between iterations (§19). Each iteration has
    its own execution, so "grouping by iteration" is a set of links to their
    replays, not a partition of one event stream."""
    run_id = execution.variables.get("run")
    if not isinstance(run_id, int):
        return None
    run = db.execute("SELECT id, title, project_id FROM ralph_runs WHERE id = ?", (run_id,)).fetchone()
    rows = db.execute(
        "SELECT number, status, verification_execution_id, changed_files, analysis, next_action, commit_sha "
        "FROM ralph_iterations WHERE run_id = ? ORDER BY number", (run_id,)
    ).fetchall()
    if run is None or run["project_id"] != execution.project_id or execution.id not in {
        r["verification_execution_id"] for r in rows
    }:
        return None
    import json

    iterations = [
        {
            "number": r["number"], "status": r["status"], "execution_id": r["verification_execution_id"],
            "files_changed": len(json.loads(r["changed_files"] or "[]")),
            "analysis": r["analysis"], "next_action": r["next_action"], "commit": r["commit_sha"],
            "current": r["verification_execution_id"] == execution.id,
        }
        for r in rows
    ]
    return {"run_id": run["id"], "title": run["title"], "iterations": iterations}
