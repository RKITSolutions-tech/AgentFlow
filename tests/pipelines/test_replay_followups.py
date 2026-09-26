"""Task 35: replay checkpoints, sub-pipeline collapsing, Ralph timeline and comparison."""
import sys
import time

import pytest

from app.db import get_db
from app.pipelines import executions, persistence, replay, visualization
from app.ralph import models as ralph
from app.ralph import timeline
from tests.conftest import (
    DESKTOP, MOBILE, assert_no_horizontal_overflow, assert_touch_target_size,
    launch_chromium, require_playwright, watch_console,
)
from tests.pipelines.test_replay import _cmd, _replayer, _run, _states, setup  # noqa: F401  (fixtures)

PY = sys.executable
AJAX = {"X-Requested-With": "XMLHttpRequest"}


# -- checkpoints ---------------------------------------------------------------------------------


def _big_execution(setup, cycles=3333):
    """A synthetic execution of 3 * cycles + 2 events (10,001 by default), bulk-inserted."""
    persistence.create_pipeline(setup.db, {"name": "big", "elements": [_cmd("only", "print(1)")]}, setup.project_id)
    eid = setup.engine.create("big", setup.project_id, 1)
    step_ids = [executions.add_step(setup.db, eid, {"name": "only", "type": "COMMAND", "phase": "MAIN"}, n + 1) for n in range(50)]
    rows = [(eid, None, "PipelineStarted", "", "2026-01-01T00:00:00")]
    for i in range(cycles):
        sid = step_ids[i % 50]
        rows += [(eid, sid, t, "", "2026-01-01T00:00:00") for t in ("StepStarted", "StepCompleted", "LoopBack")]
    rows.append((eid, None, "PipelineCompleted", "", "2026-01-01T00:00:00"))
    setup.db.executemany(
        "INSERT INTO pipeline_events (execution_id, step_execution_id, event_type, data, created_at) VALUES (?, ?, ?, ?, ?)", rows)
    setup.db.commit()
    return eid


def test_seek_folds_at_most_one_checkpoint_interval_on_10k_events(setup, monkeypatch):
    eid = _big_execution(setup)
    rp = _replayer(setup, eid)
    assert len(rp) == 10_001
    folded = []
    original = rp._fold
    monkeypatch.setattr(rp, "_fold", lambda start, lo, hi: (folded.append(hi - lo), original(start, lo, hi))[1])
    rp.state_at(9_999)  # builds every checkpoint once (a single pass over the log)
    folded.clear()
    started = time.perf_counter()
    for n in (10, 1_234, 4_999, 5_000, 7_777, 9_001, 10_001, 3):
        rp.state_at(n)
    assert max(folded) <= replay.CHECKPOINT_EVERY, folded
    assert time.perf_counter() - started < 0.5


def test_checkpointed_state_equals_a_full_fold(setup):
    eid = _big_execution(setup, cycles=700)
    rp = _replayer(setup, eid)
    for n in (0, 1, 499, 500, 501, 1_000, 1_499, len(rp)):
        fresh = _replayer(setup, eid)
        fresh._checkpoints = {0: ("PENDING", None, {}, {}), }  # force a fold from the start
        fresh._checkpoint_before = lambda k, f=fresh: (0, f._checkpoints[0])
        assert rp.state_at(n) == fresh.state_at(n), n


def test_memo_is_bounded(setup):
    rp = _replayer(setup, _big_execution(setup, cycles=200))
    for n in range(len(rp)):
        rp.state_at(n)
    assert len(rp._memo) <= replay.MEMO_SIZE


def test_controller_walks_the_visible_stops_forwards_and_back(setup):
    rp = _replayer(setup, _run(setup, _cmd("a", "print(1)"), _cmd("b", "print(2)", depends_on=["a"])))
    c = replay.ReplayController(rp)
    forward = []
    while not c.at_end:
        forward.append(c.next())
    assert forward == sorted(set(forward)) and forward[-1] == c.position
    backward = []
    while not c.at_start:
        backward.append(c.previous())
    assert backward == sorted(backward, reverse=True) and backward[-1] == 0
    assert c.seek_to_event(10**6) == forward[-1] and c.seek_to_event(0) == 0


# -- sub-pipeline collapsing ------------------------------------------------------------------------


@pytest.fixture
def composed(setup):
    persistence.create_pipeline(setup.db, {"name": "inner", "elements": [
        _cmd("db", "print('db')"), _cmd("app", "print('app')", depends_on=["db"])]}, setup.project_id)
    persistence.create_pipeline(setup.db, {"name": "outer", "elements": [
        _cmd("prep", "print(1)"),
        {"name": "setup", "type": "SUB_PIPELINE", "config": {"pipeline": "inner"}, "depends_on": ["prep"]},
        _cmd("test", "print(2)", depends_on=["setup"]),
    ]}, setup.project_id)
    eid = setup.engine.create("outer", setup.project_id, 1)
    setup.engine.run(eid)
    return setup, eid


def test_sub_pipeline_is_one_node_until_expanded(composed):
    setup, eid = composed
    ex = executions.get_execution(setup.db, eid)
    collapsed = visualization.build_graph(setup.db, ex)
    assert [n["id"] for n in collapsed["nodes"]] == ["prep", "setup", "test"]
    group = collapsed["nodes"][1]
    assert group["collapsed"] and group["type"] == "SUB_PIPELINE" and group["state"] == "PASSED"
    assert group["members"] == ["setup.db", "setup.app"] and group["summary"] == "2 of 2 steps done"
    assert {(e["from"], e["to"]) for e in collapsed["edges"]} == {("prep", "setup"), ("setup", "test")}
    assert [n["layer"] for n in collapsed["nodes"]] == [0, 1, 2]

    expanded = visualization.build_graph(setup.db, ex, expand=frozenset({"setup"}))
    assert [n["id"] for n in expanded["nodes"]] == ["prep", "setup.db", "setup.app", "test"]
    assert ("setup.app", "test") in {(e["from"], e["to"]) for e in expanded["edges"]}
    assert len(visualization.build_graph(setup.db, ex, expand=frozenset({"*"}))["nodes"]) == 4
    assert collapsed["groups"] == ["setup"]


def test_group_state_summarises_members():
    gs = visualization.group_state
    assert gs(["PASSED", "PASSED"], False) == "PASSED"
    assert gs(["PASSED", "FAILED", "PENDING"], False) == "FAILED"
    assert gs(["PASSED", "RUNNING"], True) == "RUNNING"
    assert gs(["PASSED", "PENDING"], True) == "RUNNING" and gs(["PASSED", "PENDING"], False) == "PENDING"
    assert gs(["PASSED", "WARNING"], False) == "WARNING" and gs(["DISABLED"], False) == "DISABLED"
    assert gs(["WAITING", "PENDING"], True) == "WAITING"


def test_nested_groups_expand_one_level_at_a_time(composed):
    assert visualization.representative("a.b.c", frozenset()) == "a"
    assert visualization.representative("a.b.c", frozenset({"a"})) == "a.b"
    assert visualization.representative("a.b.c", frozenset({"a", "a.b"})) == "a.b.c"
    assert visualization.representative("a.b.c", frozenset({"*"})) == "a.b.c"
    assert visualization.parse_expand("a, b.c,,") == frozenset({"a", "b.c"})


def test_group_inspector_and_replay_respect_collapse(composed):
    setup, eid = composed
    ex = executions.get_execution(setup.db, eid)
    detail = visualization.step_detail(setup.db, setup.root, ex, "setup")
    assert detail["type"] == "SUB_PIPELINE" and [m["label"] for m in detail["members"]] == ["db", "app"]
    assert visualization.step_detail(setup.db, setup.root, ex, "nope") is None
    assert visualization.step_detail(setup.db, setup.root, ex, "setup.db")["collapse"] == "setup"

    rp = _replayer(setup, eid)
    at = next(e.position for e in rp.events if e.type == "StepStarted" and e.node == "setup.db")
    state = rp.state_json(at)  # selects the event's node, which is hidden inside the group
    assert state["node"] == "setup" and state["inspector"]["type"] == "SUB_PIPELINE"
    assert _states(state["graph"])["setup"] == "RUNNING"
    assert state["graph"]["nodes"][1]["state"] == "RUNNING"
    assert rp.state_json(at, expand=frozenset({"setup"}))["node"] == "setup.db"
    # Fully replayed, the collapsed graph equals the live one.
    live = visualization.build_graph(setup.db, ex)
    assert _states(rp.graph_at(len(rp))) == _states(live)


def test_routes_pass_expand_through(composed):
    setup, eid = composed
    base = f"/projects/{setup.project_id}/pipelines/executions/{eid}"
    assert [n["id"] for n in setup.client.get(f"{base}/graph.json").get_json()["nodes"]] == ["prep", "setup", "test"]
    assert len(setup.client.get(f"{base}/graph.json?expand=setup").get_json()["nodes"]) == 4
    assert setup.client.get(f"{base}/nodes/setup.json").get_json()["members"]
    page = setup.client.get(f"{base}?expand=*").get_data(as_text=True)
    assert 'data-expand="*"' in page and "Collapse all" in page
    assert 'data-node="setup.db"' in setup.client.get(f"{base}?expand=setup").get_data(as_text=True)
    assert 'data-node="setup.db"' not in setup.client.get(base).get_data(as_text=True)
    seek = setup.client.post(f"{base}/replay/seek", data={"action": "last", "expand": "*"}, headers=AJAX).get_json()
    assert len(seek["graph"]["nodes"]) == 4


# -- Ralph timeline and comparison --------------------------------------------------------------------


@pytest.fixture
def ralph_run(setup):
    persistence.create_pipeline(setup.db, {"name": "verify", "elements": [
        _cmd("build", "print(1)"), _cmd("unit", "import sys; sys.exit(int(sys.argv[1]))", depends_on=["build"])]}, setup.project_id)
    persistence.create_pipeline(setup.db, {"name": "verify2", "elements": [
        _cmd("build", "print(1)"), _cmd("unit", "print(2)", depends_on=["build"]), _cmd("e2e", "print(3)", depends_on=["unit"])]}, setup.project_id)
    run_id = ralph.create_run(setup.db, setup.project_id, 1, "Task", "do it", "verify")
    ids = []
    for n, (pipeline, status, files, reply) in enumerate([
        ("verify", "FAILED", ["a.py", "b.py"], "first try\nsame line"),
        ("verify2", "PASSED", ["b.py", "c.py"], "second try\nsame line"),
    ], start=1):
        eid = setup.engine.create(pipeline, setup.project_id, 1, variables={"run": run_id, "iteration": n})
        setup.engine.run(eid)
        it = ralph.add_iteration(setup.db, run_id, n, f"prompt v{n}\ncommon", False)
        ralph.update_iteration(setup.db, it, verification_execution_id=eid, status=status, changed_files=files,
                               reply=reply, analysis=f"analysis {n}", commit_sha="abc123def456" if n == 2 else "")
        ids.append(eid)
    return setup, run_id, ids


def test_merged_timeline_has_a_lane_per_iteration_in_time_order(ralph_run):
    setup, run_id, ids = ralph_run
    data = timeline.merged_timeline(setup.db, run_id)
    assert [(l["number"], l["execution_id"]) for l in data["lanes"]] == [(1, ids[0]), (2, ids[1])]
    assert all(l["events"] for l in data["lanes"])
    assert {e["iteration"] for e in data["events"]} == {1, 2}
    assert [e["at"] for e in data["events"]] == sorted(e["at"] for e in data["events"])
    first = data["lanes"][0]["events"][0]
    assert first["execution_id"] == ids[0] and first["position"] == 1 and first["visible"]
    assert len(timeline.merged_timeline(setup.db, run_id, include_hidden=True)["events"]) >= len(data["events"])


def test_compare_two_iterations(ralph_run):
    setup, run_id, _ = ralph_run
    c = timeline.compare(setup.db, run_id, 1, 2)
    assert c["files"] == {"only_a": ["a.py"], "only_b": ["c.py"], "both": ["b.py"]}
    steps = {s["name"]: s for s in c["steps"]}
    assert steps["build"]["changed"] is False and steps["unit"]["changed"] is True
    assert (steps["unit"]["a"], steps["unit"]["b"]) == ("FAILED", "PASSED") or steps["unit"]["a"] != steps["unit"]["b"]
    assert steps["e2e"]["a"] == "—" and steps["e2e"]["changed"]
    kinds = {(l["kind"], l["text"]) for l in c["prompt_diff"]}
    assert ("del", "prompt v1") in kinds and ("add", "prompt v2") in kinds
    assert timeline.compare(setup.db, run_id, 1, 9) is None
    assert timeline.compare(setup.db, run_id, 1, 1)["prompt_diff"] == []


def test_timeline_and_compare_pages(ralph_run):
    setup, run_id, ids = ralph_run
    base = f"/projects/{setup.project_id}/ralph/{run_id}"
    page = setup.client.get(f"{base}/timeline").get_data(as_text=True)
    assert 'data-lane="1"' in page and 'data-lane="2"' in page and f"executions/{ids[0]}#replay=1" in page
    assert setup.client.get(f"{base}/timeline", headers=AJAX).get_json()["lanes"]
    cmp_page = setup.client.get(f"{base}/compare").get_data(as_text=True)
    assert "Compare iterations" in cmp_page and "prompt v2" in cmp_page and "e2e" in cmp_page
    assert setup.client.get(f"{base}/compare?a=1&b=99").status_code == 404
    assert setup.client.get(f"{base}/compare?a=x").status_code == 400
    assert setup.client.get(f"/projects/{setup.project_id}/ralph/9999/timeline").status_code == 404
    assert "Compare iterations" in setup.client.get(base).get_data(as_text=True)
    # A one-iteration run redirects instead of comparing.
    single = ralph.create_run(setup.db, setup.project_id, 1, "One", "x", "verify")
    ralph.add_iteration(setup.db, single, 1, "p", False)
    assert setup.client.get(f"/projects/{setup.project_id}/ralph/{single}/compare").status_code == 302


# -- real browser --------------------------------------------------------------------------------------


@pytest.fixture
def browser_world(app, client, tmp_path, live_server):
    from app.agents.fake import FakeAgentAdapter
    from app.execution.host import HostExecutionProvider
    from app.pipelines.engine import PipelineEngine
    from tests.conftest import create_project_with_repo

    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        engine = PipelineEngine(db, provider, str(tmp_path / "artifacts"), lambda c: FakeAgentAdapter(c), sleep=lambda s: None)
        persistence.create_pipeline(db, {"name": "inner", "elements": [_cmd("db", "print(1)"), _cmd("app", "print(2)", depends_on=["db"])]}, project_id)
        persistence.create_pipeline(db, {"name": "outer", "elements": [
            _cmd("prep", "print(1)"),
            {"name": "setup", "type": "SUB_PIPELINE", "config": {"pipeline": "inner"}, "depends_on": ["prep"]},
            _cmd("test", "print(2)", depends_on=["setup"])]}, project_id)
        eid = engine.create("outer", project_id, 1)
        engine.run(eid)
        run_id = ralph.create_run(db, project_id, 1, "Task", "do it", "outer")
        for n in (1, 2):
            vid = engine.create("outer", project_id, 1, variables={"run": run_id, "iteration": n})
            engine.run(vid)
            it = ralph.add_iteration(db, run_id, n, f"prompt {n}\nshared", False)
            ralph.update_iteration(db, it, verification_execution_id=vid, status="FAILED" if n == 1 else "PASSED",
                                   changed_files=["long/" + "x" * 50 + f"{n}.py"], reply=f"reply {n}")
    base = f"{live_server}/projects/{project_id}"
    return type("W", (), {"execution": f"{base}/pipelines/executions/{eid}", "timeline": f"{base}/ralph/{run_id}/timeline",
                          "compare": f"{base}/ralph/{run_id}/compare", "run_id": run_id})


@pytest.mark.parametrize("viewport", [MOBILE, DESKTOP], ids=["375", "1280"])
def test_group_timeline_and_compare_pages_in_a_browser(browser_world, viewport):
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=viewport)
            errors = watch_console(page)
            page.goto(browser_world.execution)
            page.wait_for_selector(".pipeline-edges path", state="attached")
            assert page.locator("[data-node]").count() == 3
            page.click('[data-node="setup"]')
            page.wait_for_selector("[data-inspector] h2:has-text('setup')")
            page.click("[data-inspector] button:has-text('Expand setup')")
            page.wait_for_url("**expand=setup**")
            page.wait_for_selector('[data-node="setup.db"]')
            assert page.locator("[data-node]").count() == 4
            page.click('[data-node="setup.db"]')
            page.wait_for_selector("[data-inspector] button:has-text('Collapse setup')")
            page.click("[data-inspector] button:has-text('Collapse setup')")
            page.wait_for_selector('[data-node="setup"]')
            assert page.locator("[data-node]").count() == 3
            page.click("[data-replay-toggle]")  # replay inside a collapsed graph
            page.wait_for_selector(".replay-event")
            page.click("[data-replay-action=last]")
            page.wait_for_function("document.querySelector('[data-node=setup] .node-state').textContent.includes('Passed')")
            assert_no_horizontal_overflow(page, "collapsed replay")
            for name, url in (("timeline", browser_world.timeline), ("compare", browser_world.compare)):
                page.goto(url)
                page.wait_for_load_state("networkidle")
                assert_no_horizontal_overflow(page, name)
                if viewport is MOBILE:
                    assert_touch_target_size(page, label=name)
            page.goto(browser_world.timeline)
            page.locator('[data-lane="2"] .lane-event').nth(3).click()  # opens that execution's replay at that event
            page.wait_for_selector(".replay-event")
            page.wait_for_function("document.querySelector('[data-replay-position]').textContent.startsWith('Event 4')")
            assert errors == []
        finally:
            browser.close()
