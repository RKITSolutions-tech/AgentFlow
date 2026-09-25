from __future__ import annotations

import pytest

from app.agents import models as agent_models
from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.projects import models as project_models

OPTIONS = [
    {"label": "Postgres (Recommended)", "description": "Robust"},
    {"label": "SQLite", "description": "Simple"},
]


def _session(db):
    project_id = project_models.create_project(db, "Question Project")
    return agent_models.create_agent_session(db, project_id, "fake")


def test_create_question_emits_event_and_adds_other(app):
    with app.app_context():
        db = get_db()
        session_id = _session(db)

        q = agent_models.create_clarifying_question(
            db, session_id, "Which database?", OPTIONS, header="Database"
        )

        assert q.status == agent_models.QUESTION_PENDING
        assert q.options == OPTIONS
        assert q.event_id is not None
        labels = [o["label"] for o in q.display_options()]
        assert labels == ["Postgres (Recommended)", "SQLite", agent_models.OTHER_OPTION_LABEL]
        # Other is never stored with the agent's own options.
        assert len(q.options) == 2

        events = agent_models.list_agent_events(db, session_id)
        assert [e.event_type for e in events] == ["ClarifyingQuestion"]
        assert events[0].id == q.event_id


def test_string_options_are_normalized(app):
    with app.app_context():
        db = get_db()
        q = agent_models.create_clarifying_question(db, _session(db), "Pick", ["a", "b"])
        assert q.options == [
            {"label": "a", "description": ""},
            {"label": "b", "description": ""},
        ]


@pytest.mark.parametrize("options", [[], [""], [{"label": "  "}]])
def test_invalid_options_rejected(app, options):
    with app.app_context():
        db = get_db()
        session_id = _session(db)
        with pytest.raises(ValueError):
            agent_models.create_clarifying_question(db, session_id, "Pick", options)
        assert agent_models.list_agent_events(db, session_id) == []


def test_blank_question_rejected(app):
    with app.app_context():
        db = get_db()
        with pytest.raises(ValueError):
            agent_models.create_clarifying_question(db, _session(db), "  ", ["a"])


def test_answer_with_option_and_other_text(app):
    with app.app_context():
        db = get_db()
        session_id = _session(db)
        q1 = agent_models.create_clarifying_question(db, session_id, "DB?", OPTIONS)
        q2 = agent_models.create_clarifying_question(db, session_id, "DB?", OPTIONS)

        a1 = agent_models.answer_clarifying_question(db, q1.id, selected=["SQLite"])
        assert a1.status == agent_models.QUESTION_ANSWERED
        assert a1.answer == {"selected": ["SQLite"], "other": ""}
        assert a1.answered_at is not None

        a2 = agent_models.answer_clarifying_question(db, q2.id, other_text=" MySQL ")
        assert a2.answer == {"selected": [], "other": "MySQL"}


def test_answer_validation(app):
    with app.app_context():
        db = get_db()
        q = agent_models.create_clarifying_question(db, _session(db), "DB?", OPTIONS)

        with pytest.raises(ValueError):
            agent_models.answer_clarifying_question(db, q.id)
        with pytest.raises(ValueError):
            agent_models.answer_clarifying_question(db, q.id, selected=["Oracle"])
        with pytest.raises(ValueError):
            agent_models.answer_clarifying_question(
                db, q.id, selected=["SQLite", "Postgres (Recommended)"]
            )
        assert agent_models.get_clarifying_question(db, q.id).status == "PENDING"


def test_multi_select_allows_several(app):
    with app.app_context():
        db = get_db()
        q = agent_models.create_clarifying_question(
            db, _session(db), "Which?", OPTIONS, multi_select=True
        )
        answered = agent_models.answer_clarifying_question(
            db, q.id, selected=["SQLite", "Postgres (Recommended)"]
        )
        assert answered.multi_select is True
        assert len(answered.answer["selected"]) == 2


def test_skip_is_distinct_from_answer_and_final(app):
    with app.app_context():
        db = get_db()
        q = agent_models.create_clarifying_question(db, _session(db), "DB?", OPTIONS)

        skipped = agent_models.skip_clarifying_question(db, q.id)
        assert skipped.status == agent_models.QUESTION_SKIPPED
        assert skipped.answer is None

        with pytest.raises(ValueError):
            agent_models.answer_clarifying_question(db, q.id, selected=["SQLite"])
        with pytest.raises(ValueError):
            agent_models.skip_clarifying_question(db, q.id)


def test_pending_state_reconstructed_from_database_on_resume(app):
    """State lives in SQLite, so a fresh adapter/connection sees pending questions."""
    with app.app_context():
        db = get_db()
        session_id = _session(db)
        answered = agent_models.create_clarifying_question(db, session_id, "One?", ["a"])
        agent_models.answer_clarifying_question(db, answered.id, selected=["a"])
        pending = agent_models.create_clarifying_question(db, session_id, "Two?", ["x", "y"])

        FakeAgentAdapter(db)  # new adapter instance holds no question state
        found = agent_models.list_clarifying_questions(
            db, session_id, status=agent_models.QUESTION_PENDING
        )
        assert [q.id for q in found] == [pending.id]
        assert len(agent_models.list_clarifying_questions(db, session_id)) == 2


def test_deleting_session_removes_questions(app):
    with app.app_context():
        db = get_db()
        session_id = _session(db)
        q = agent_models.create_clarifying_question(db, session_id, "DB?", OPTIONS)
        agent_models.delete_agent_session(db, session_id)
        assert agent_models.get_clarifying_question(db, q.id) is None
