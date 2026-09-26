import pytest

from app.agents.fake import FakeAgentAdapter
from app.agents.base import AgentContext
from app.backlog import persistence as backlog
from app.db import get_db
from app.sprints import persistence as sprints
from app.sprints import readiness, workflow
from app.sprints.models import InvalidSprintTransitionError, NotReadyError
from app.sprints.planning_agent import (
    PlanningAgent,
    PlanParseError,
    parse_proposal,
    scripted_plan,
)


@pytest.fixture
def db(app):
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        conn.commit()
        yield conn


def _sprint_with_items(db, n=2, goal="Ship it"):
    sprint_id = sprints.create_sprint(db, 1, "S1", goal=goal)
    ids = [backlog.create_item(db, 1, text=f"item {i}") for i in range(n)]
    workflow.select_items(db, sprint_id, ids)
    return sprint_id, ids


def _plan(db, sprint_id):
    agent = PlanningAgent(FakeAgentAdapter(db), fake_script_from_items=True)
    workflow.start_planning(db, sprint_id, agent, AgentContext(project_id=1, working_directory="."))
    return agent, workflow.ingest_proposal(db, sprint_id, agent)


def test_create_and_status_transitions(db):
    sprint_id = sprints.create_sprint(db, 1, " Sprint ", goal="g")
    assert sprints.get_sprint(db, sprint_id).name == "Sprint"
    sprints.set_status(db, sprint_id, "PLANNING")
    with pytest.raises(InvalidSprintTransitionError):
        sprints.set_status(db, sprint_id, "COMPLETE")
    with pytest.raises(ValueError):
        sprints.create_sprint(db, 1, "  ")
    with pytest.raises(ValueError):
        sprints.create_sprint(db, 1, "x", planning_profile="NOPE")


def test_select_items_moves_backlog_status(db):
    sprint_id, ids = _sprint_with_items(db)
    assert {backlog.get_item(db, i).status for i in ids} == {"SELECTED"}
    assert backlog.get_item(db, ids[0]).sprint_id == sprint_id
    other = sprints.create_sprint(db, 1, "S2")
    with pytest.raises(ValueError):
        workflow.select_items(db, other, [ids[0]])
    workflow.deselect_item(db, sprint_id, ids[0])
    assert backlog.get_item(db, ids[0]).status == "TRIAGED"


def test_planning_requires_goal_and_items(db):
    agent = PlanningAgent(FakeAgentAdapter(db), fake_script_from_items=True)
    ctx = AgentContext(project_id=1, working_directory=".")
    empty = sprints.create_sprint(db, 1, "E", goal="g")
    with pytest.raises(ValueError, match="at least one"):
        workflow.start_planning(db, empty, agent, ctx)
    no_goal, _ = _sprint_with_items(db, goal="")
    with pytest.raises(ValueError, match="goal"):
        workflow.start_planning(db, no_goal, agent, ctx)


def test_planning_agent_generates_task_graph(db):
    sprint_id, ids = _sprint_with_items(db, 3)
    _, outcome = _plan(db, sprint_id)
    assert outcome == "COMPLETE"
    work = sprints.list_work_items(db, sprint_id)
    assert len(work) == 3 and all(w.status == "SUGGESTED" for w in work)
    assert sorted(i for w in work for i in w.backlog_item_ids) == sorted(ids)
    assert sprints.get_sprint(db, sprint_id).status == "REVIEW"
    assert {backlog.get_item(db, i).status for i in ids} == {"PLANNED"}
    assert sprints.get_sprint(db, sprint_id).planning_session_id is not None


def test_readiness_blocks_until_reviewed_then_approves(db):
    sprint_id, ids = _sprint_with_items(db, 2)
    _plan(db, sprint_id)
    failing = {n for n, s, _ in readiness.evaluate(db, sprint_id) if s == "FAIL"}
    assert failing == {"acceptance_reviewed"}
    with pytest.raises(NotReadyError):
        workflow.approve(db, sprint_id, "Sam")
    for w in sprints.list_work_items(db, sprint_id):
        sprints.update_work_item(db, w.id, status="REVIEWED")
    with pytest.raises(ValueError, match="name"):
        workflow.approve(db, sprint_id, " ")
    workflow.approve(db, sprint_id, "Sam", "looks good")
    sprint = sprints.get_sprint(db, sprint_id)
    assert (sprint.status, sprint.approved_by) == ("READY", "Sam") and sprint.approved_at
    assert {backlog.get_item(db, i).status for i in ids} == {"READY"}
    assert {w.status for w in sprints.list_work_items(db, sprint_id)} == {"APPROVED"}
    assert [a.decision for a in sprints.list_approvals(db, sprint_id)] == ["APPROVED"]

    workflow.revoke(db, sprint_id, "Sam", "changed mind")
    assert sprints.get_sprint(db, sprint_id).status == "REVIEW"
    assert sprints.get_sprint(db, sprint_id).approved_at is None
    assert [a.decision for a in sprints.list_approvals(db, sprint_id)] == ["APPROVED", "REVOKED"]


def test_editing_suggested_task_marks_it_reviewed(db):
    sprint_id, _ = _sprint_with_items(db, 1)
    _plan(db, sprint_id)
    work = sprints.list_work_items(db, sprint_id)[0]
    sprints.update_work_item(db, work.id, acceptance=["a", " ", "b"])
    updated = sprints.get_work_item(db, work.id)
    assert (updated.status, updated.acceptance) == ("REVIEWED", ["a", "b"])


def test_dependency_validation(db):
    sprint_id = sprints.create_sprint(db, 1, "S", goal="g")
    a, b, c = (sprints.add_work_item(db, sprint_id, t) for t in "abc")
    sprints.set_dependencies(db, b, [a])
    sprints.set_dependencies(db, c, [a, b])
    assert sprints.get_work_item(db, c).depends_on == [a, b]
    with pytest.raises(ValueError, match="cycle"):
        sprints.set_dependencies(db, a, [c])
    with pytest.raises(ValueError, match="itself"):
        sprints.set_dependencies(db, a, [a])
    assert sprints.find_cycle({1: {2}, 2: {1}})


def test_replan_keeps_human_edited_tasks(db):
    sprint_id, _ = _sprint_with_items(db, 2)
    agent, _ = _plan(db, sprint_id)
    kept = sprints.list_work_items(db, sprint_id)[0]
    sprints.update_work_item(db, kept.id, title="Mine")
    workflow.start_planning(
        db, sprint_id, agent, AgentContext(project_id=1, working_directory="."), replan=True
    )
    assert workflow.ingest_proposal(db, sprint_id, agent) == "COMPLETE"
    titles = [w.title for w in sprints.list_work_items(db, sprint_id)]
    assert "Mine" in titles and len(titles) == 3  # 1 kept + 2 fresh suggestions


def test_bad_agent_reply_returns_sprint_to_draft(db):
    sprint_id, ids = _sprint_with_items(db, 1)
    adapter = FakeAgentAdapter(db)

    class Broken(PlanningAgent):
        def start(self, context, sprint, items, docs):
            session = adapter.start(
                context, "p", {"role": "PLANNING", "script": [{"action": "message", "text": "no json"}]}
            )
            return session.id

    agent = Broken(adapter)
    workflow.start_planning(db, sprint_id, agent, AgentContext(project_id=1, working_directory="."))
    with pytest.raises(workflow.PlanningFailed):
        workflow.ingest_proposal(db, sprint_id, agent)
    assert sprints.get_sprint(db, sprint_id).status == "DRAFT"
    assert backlog.get_item(db, ids[0]).status == "SELECTED"


def test_parse_proposal_variants():
    text = 'prose ```json\n{"tasks": [{"title": "A", "backlog_items": [1, 99]}]}\n``` end'
    tasks = parse_proposal(text, {1})
    assert tasks[0].backlog_items == [1] and tasks[0].ref == "T1"
    assert parse_proposal('{"tasks": [{"title": "B"}]}', set())[0].title == "B"
    for bad in ("nothing", '{"tasks": []}', '{"tasks": [{"ref": "x"}]}',
                '{"tasks": [{"title":"a","ref":"r"},{"title":"b","ref":"r"}]}'):
        with pytest.raises(PlanParseError):
            parse_proposal(bad, set())


def test_scripted_plan_covers_every_item(db):
    ids = [backlog.create_item(db, 1, text=f"t{i}") for i in range(2)]
    items = [backlog.get_item(db, i) for i in ids]
    script = scripted_plan(items)
    assert script[-1] == {"action": "complete"}
    assert len(parse_proposal(script[0]["text"], set(ids))) == 2
