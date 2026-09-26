import sys
import time

import pytest

from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.pipelines import executions, persistence, replay, visualization
from app.pipelines.engine import PipelineEngine
from tests.conftest import create_project_with_repo, launch_chromium, require_playwright

PY = sys.executable
AJAX = {"X-Requested-With": "XMLHttpRequest"}


def _cmd(name, code, **extra):
    return {"name": name, "type": "COMMAND", "config": {"command": f'{PY} -c "{code}"'}, **extra}


@pytest.fixture
def setup(app, client, tmp_path):
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        engine = PipelineEngine(get_db(), provider, str(tmp_path / "artifacts"), lambda c: FakeAgentAdapter(c), sleep=lambda s: None)
        yield type("S", (), {
            "app": app, "client": client, "project_id": project_id, "engine": engine, "db": get_db(),
            "root": str(tmp_path / "artifacts"),
        })


def _run(setup, *elements, name="v"):
    persistence.create_pipeline(setup.db, {"name": name, "elements": list(elements)}, setup.project_id)
    eid = setup.engine.create(name, setup.project_id, 1)
    setup.engine.run(eid)
    return eid


def _replayer(setup, eid):
    return replay.Replayer(setup.db, executions.get_execution(setup.db, eid))


def _states(graph):
    return {n["id"]: n["state"] for n in graph["nodes"]}


def _position(rp, type_, node=None):
    return next(e.position for e in rp.events if e.type == type_ and (node is None or e.node == node))


def test_reconstruct_state_at_each_checkpoint(setup):
    eid = _run(setup, _cmd("build", "print(1)"), _cmd("test", "print(2)", depends_on=["build"]))
    rp = _replayer(setup, eid)
    assert _states(rp.graph_at(0)) == {"build": "PENDING", "test": "PENDING"}
    assert rp.graph_at(0)["execution"]["status"] == "PENDING"
    mid = _position(rp, "StepStarted", "build")
    assert _states(rp.graph_at(mid)) == {"build": "RUNNING", "test": "PENDING"}
    done = _position(rp, "StepCompleted", "build")
    assert _states(rp.graph_at(done)) == {"build": "PASSED", "test": "PENDING"}
    assert _states(rp.graph_at(len(rp))) == {"build": "PASSED", "test": "PASSED"}
    assert rp.graph_at(len(rp))["execution"]["status"] == "COMPLETED"
    assert rp.graph_at(mid)["execution"]["status"] == "RUNNING"


def test_final_replay_matches_the_live_graph(setup):
    eid = _run(
        setup,
        _cmd("a", "print(1)"),
        _cmd("flaky", "import sys; sys.exit(1)", compensation={"action": "CONTINUE"}, depends_on=["a"]),
        _cmd("after", "print(2)", depends_on=["flaky"]),
        _cmd("off", "print(3)", enabled="DISABLED"),
        {"name": "gate", "type": "MANUAL_APPROVAL", "config": {"prompt": "?"}, "depends_on": ["a"]},
    )
    ex = executions.get_execution(setup.db, eid)
    live = visualization.build_graph(setup.db, ex)
    replayed = _replayer(setup, eid).graph_at(10_000)  # past the end clamps
    assert _states(replayed) == _states(live)
    assert replayed["execution"]["status"] == live["execution"]["status"] == "PAUSED"
    assert replayed["execution"]["waiting_step_id"] == live["execution"]["waiting_step_id"]


def test_reconstruct_with_compensation_state(setup):
    eid = _run(
        setup,
        _cmd("fix", "print(1)"),
        _cmd("check", "import sys; sys.exit(1)", compensation={"action": "LOOP", "step": "fix", "max_loops": 2}),
    )
    rp = _replayer(setup, eid)
    types = [e.type for e in rp.events]
    assert "LoopBack" in types and "CompensationStarted" in types
    first_loop = _position(rp, "LoopBack")
    assert rp.state_at(first_loop - 1)["loops"] == {}
    assert rp.state_at(first_loop)["loops"] == {"check": 1}
    assert rp.state_at(len(rp))["loops"] == {"check": types.count("LoopBack")}
    # Retries: later replays show the later attempt for the looped step.
    early = {n["id"]: n["attempts"] for n in rp.graph_at(first_loop)["nodes"]}
    late = {n["id"]: n["attempts"] for n in rp.graph_at(len(rp))["nodes"]}
    assert early["check"] == 1 and late["check"] > early["check"]


def test_manual_approval_pause_and_resume(setup):
    eid = _run(setup, {"name": "gate", "type": "MANUAL_APPROVAL", "config": {"prompt": "?"}})
    step = executions.list_steps(setup.db, eid)[0]
    setup.engine.resolve_manual(step.id, "APPROVED", "Sam")
    setup.engine.run(eid)
    rp = _replayer(setup, eid)
    asked = _position(rp, "ManualApprovalRequested")
    assert rp.graph_at(asked)["execution"]["status"] == "PAUSED"
    assert _states(rp.graph_at(asked)) == {"gate": "WAITING"}
    assert rp.state_at(asked)["waiting_step_id"] == step.id
    given = _position(rp, "ManualApprovalReceived")
    assert _states(rp.graph_at(given)) == {"gate": "PASSED"}
    # The decision is recorded but the execution stays paused until it resumes.
    assert rp.state_at(given)["waiting_step_id"] == step.id and rp.state_at(given)["status"] == "PAUSED"
    resumed = rp.state_at(_position(rp, "PipelineResumed"))
    assert resumed["waiting_step_id"] is None and resumed["status"] == "RUNNING"
    assert rp.graph_at(len(rp))["execution"]["status"] == "COMPLETED"


def test_step_output_appears_only_after_the_step_finishes(setup):
    eid = _run(setup, _cmd("build", "print('hello-output')"))
    rp = _replayer(setup, eid)
    started = _position(rp, "StepStarted")
    finished = _position(rp, "StepCompleted")
    running = rp.inspector_at(started, "build", setup.root)
    assert running["state"] == "RUNNING" and running["attempts"][0]["output"] == ""
    assert [e["type"] for e in running["events"]] == ["StepStarted"]
    done = rp.inspector_at(finished, "build", setup.root)
    assert done["state"] == "PASSED" and "hello-output" in done["attempts"][0]["output"]
    assert [e["type"] for e in done["events"]] == ["StepStarted", "StepCompleted"]


def test_artifacts_as_of_event(setup):
    eid = _run(setup, _cmd("build", "print('log me')"), _cmd("next", "print(2)", depends_on=["build"]))
    rp = _replayer(setup, eid)
    assert rp.artifacts_at(0) == []
    after_build = _position(rp, "StepCompleted", "build")
    names = [a["name"] for a in rp.artifacts_at(after_build)]
    assert names and all(n.startswith("build") for n in names)
    assert len(rp.artifacts_at(len(rp))) > len(names)


def test_timeline_events_serialisation_and_kinds(setup):
    eid = _run(setup, _cmd("build", "print(1)"))
    rp = _replayer(setup, eid)
    page = rp.timeline(limit=100)
    assert page["total"] == len(rp) and page["events"][0]["position"] == 1
    first = page["events"][0]
    assert first["type"] == "PipelineStarted" and first["kind"] == "milestone" and first["at"]
    steps = rp.timeline(kind="step")
    assert {e["kind"] for e in steps["events"]} == {"step"} and steps["total"] < page["total"]
    assert "milestone" in page["kinds"]
    assert len(rp.timeline(offset=1, limit=1)["events"]) == 1


def test_controller_navigation(setup):
    eid = _run(setup, _cmd("a", "print(1)"), _cmd("b", "print(2)", depends_on=["a"]))
    rp = _replayer(setup, eid)
    c = replay.ReplayController(rp)
    assert c.position == 0 and c.at_start and c.previous() == 0
    assert c.next() == rp.events[0].position
    assert c.last() == len(rp) and c.at_end and c.next() == len(rp)
    assert c.previous() == len(rp) - 1
    assert c.seek_to_event(3) == 3 and c.first() == 0
    assert c.apply("seek", 2) == 2 and c.apply("next") == 3
    with pytest.raises(ValueError):
        c.apply("teleport")
    with pytest.raises(ValueError):
        c.apply("seek")


def test_controller_skips_hidden_events(setup):
    eid = _run(setup, _cmd("a", "print(1)"))
    rp = _replayer(setup, eid)
    hidden = [e for e in rp.events if not e.visible]
    if not hidden:  # no housekeeping events recorded: mark one to check the skip
        rp.events[2].visible = False
        hidden = [rp.events[2]]
    c = replay.ReplayController(rp)
    seen = []
    while not c.at_end:
        seen.append(c.next())
    assert hidden[0].position not in seen


def test_controller_playback_state(setup):
    eid = _run(setup, _cmd("a", "print(1)"))
    rp = _replayer(setup, eid)
    c = replay.ReplayController(rp)
    assert not c.playing
    c.play(2.0)
    assert c.playing and c.speed == 2.0 and c.interval_ms(1000) == 500
    assert c.tick() > 0
    c.pause()
    held = c.position
    assert c.tick() == held  # paused: no movement
    with pytest.raises(ValueError):
        c.play(3.0)
    c.play(1.0)
    while c.playing and not c.at_end:
        c.tick()
    c.tick()
    assert c.at_end and not c.playing  # reaching the end stops playback
    c.play(1.0)
    assert c.position == 0 and c.playing  # playing a finished tape restarts it


def test_ralph_iteration_grouping(setup):
    from app.ralph import models as ralph

    persistence.create_pipeline(setup.db, {"name": "verify", "elements": [_cmd("t", "print(1)")]}, setup.project_id)
    run_id = ralph.create_run(setup.db, setup.project_id, 1, "Task", "do", "verify")
    ids = []
    for n in (1, 2):
        eid = setup.engine.create("verify", setup.project_id, 1, variables={"run": run_id, "iteration": n})
        setup.engine.run(eid)
        it = ralph.add_iteration(setup.db, run_id, n, "p", False)
        ralph.update_iteration(setup.db, it, verification_execution_id=eid, status="FAILED" if n == 1 else "PASSED",
                               changed_files=["a.py"], analysis="checked")
        ids.append(eid)
    ex = executions.get_execution(setup.db, ids[1])
    group = replay.ralph_iterations(setup.db, ex)
    assert group["run_id"] == run_id
    assert [(i["number"], i["execution_id"], i["current"]) for i in group["iterations"]] == [(1, ids[0], False), (2, ids[1], True)]
    assert group["iterations"][0]["files_changed"] == 1 and group["iterations"][0]["status"] == "FAILED"
    # A "run" variable that no iteration of that run produced is not a Ralph execution.
    plain = setup.engine.create("verify", setup.project_id, 1, variables={"run": run_id})
    assert replay.ralph_iterations(setup.db, executions.get_execution(setup.db, plain)) is None
    page = setup.client.get(f"/projects/{setup.project_id}/pipelines/executions/{ids[1]}").get_data(as_text=True)
    assert "Iteration 1" in page and "Iteration 2" in page and 'aria-current="page"' in page


def test_replay_routes(setup):
    eid = _run(setup, _cmd("a", "print(1)"), _cmd("b", "print(2)", depends_on=["a"]))
    base = f"/projects/{setup.project_id}/pipelines/executions/{eid}/replay"
    c = setup.client
    events = c.get(f"{base}/events").get_json()
    assert events["total"] > 3 and events["events"][0]["position"] == 1
    total = events["total"]
    state = c.get(f"{base}/state?event=2").get_json()
    assert state["event"] == 2 and state["total"] == total and state["graph"]["replay"]["event"] == 2
    assert c.get(f"{base}/state?event=-1").status_code == 400
    assert c.get(f"{base}/state?event={total + 1}").status_code == 400
    assert c.get(f"{base}/state").status_code == 400

    moved = c.post(f"{base}/seek", data={"action": "next", "current": 0}, headers=AJAX).get_json()
    assert moved["event"] == 1 and moved["at_start"] is False and moved["at_end"] is False
    back = c.post(f"{base}/seek", data={"action": "previous", "current": 1}).get_json()
    assert back["event"] == 0 and back["at_start"] is True and back["inspector"] is None
    end = c.post(f"{base}/seek", data={"action": "last"}).get_json()
    assert end["event"] == total and end["at_end"] is True
    stepped = c.post(f"{base}/seek", data={"action": "seek", "event": 3}).get_json()
    assert stepped["node"] and stepped["inspector"]["name"] == stepped["node"]
    assert c.post(f"{base}/seek", data={"action": "nope"}).status_code == 400
    # Scoped to its project.
    assert c.get(f"/projects/{setup.project_id + 50}/pipelines/executions/{eid}/replay/events").status_code == 404


def test_execution_page_offers_replay(setup):
    eid = _run(setup, _cmd("a", "print(1)"))
    page = setup.client.get(f"/projects/{setup.project_id}/pipelines/executions/{eid}").get_data(as_text=True)
    assert "data-replay-toggle" in page and "data-replay-timeline" in page


def test_large_event_log_replays_quickly(setup):
    eid = _run(setup, _cmd("a", "print(1)"))
    ex = executions.get_execution(setup.db, eid)
    step = executions.list_steps(setup.db, eid)[0]
    setup.db.executemany(
        "INSERT INTO pipeline_events (execution_id, step_execution_id, event_type, data, created_at) VALUES (?, ?, 'StepStarted', '', '2026-01-01T00:00:00')",
        [(eid, step.id)] * 1200,
    )
    setup.db.commit()
    started = time.perf_counter()
    rp = replay.Replayer(setup.db, ex)
    assert len(rp) > 1200
    for n in range(0, len(rp), 100):
        rp.graph_at(n)
    assert rp.timeline(limit=1000)["total"] > 1200
    assert time.perf_counter() - started < 2.0


# -- real browser -----------------------------------------------------------------------


@pytest.fixture
def browser_execution(app, client, tmp_path, live_server):
    project_id, _ = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        engine = PipelineEngine(db, provider, str(tmp_path / "artifacts"), lambda c: FakeAgentAdapter(c), sleep=lambda s: None)
        persistence.create_pipeline(
            db, {"name": "v", "elements": [_cmd("build", "print(1)"), _cmd("test", "print(2)", depends_on=["build"])]}, project_id
        )
        eid = engine.create("v", project_id, 1)
        engine.run(eid)
    return f"{live_server}/projects/{project_id}/pipelines/executions/{eid}"


def _open_replay(page, url):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.click("[data-replay-toggle]")
    page.wait_for_selector(".replay-event")
    return errors


@pytest.mark.parametrize("viewport", [{"width": 375, "height": 667}, {"width": 1280, "height": 800}], ids=["375", "1280"])
def test_replay_timeline_renders_and_click_seeks(browser_execution, viewport):
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=viewport)
            errors = _open_replay(page, browser_execution)
            assert page.locator(".replay-event").count() > 3
            # Position 0: nothing has run yet.
            assert "Pending" in page.inner_text('[data-node="build"] .node-state')
            # Click the completed-build event: graph and inspector follow.
            page.locator(".replay-event", has_text="Step Completed · build").first.click()
            page.wait_for_function("document.querySelector('[data-node=build] .node-state').textContent.includes('Passed')")
            assert "Pending" in page.inner_text('[data-node="test"] .node-state')
            assert "build" in page.inner_text("[data-inspector] h2")
            assert page.locator('.replay-event[aria-current="true"]').count() == 1
            # The page itself never scrolls sideways; the timeline strip does.
            assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
            for control in page.locator(".replay-controls button").all():
                assert control.bounding_box()["height"] >= 40
            page.click("[data-replay-exit]")
            assert "Passed" in page.inner_text('[data-node="test"] .node-state')
            assert errors == []
        finally:
            browser.close()


def test_replay_playback_animates_through_events(browser_execution):
    sync_playwright = require_playwright()
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            _open_replay(page, browser_execution)
            page.select_option("[data-replay-speed]", "4")
            page.click("[data-replay-play]")
            page.wait_for_function(
                "document.querySelector('[data-node=test] .node-state').textContent.includes('Passed')", timeout=15000
            )
            page.wait_for_function("document.querySelector('[data-replay-play]').getAttribute('aria-pressed') === 'false'")
        finally:
            browser.close()
