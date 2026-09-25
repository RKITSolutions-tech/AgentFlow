from __future__ import annotations

import json

from app.agents import models as agent_models
from app.agents.base import AgentContext
from app.agents.codex import CodexAdapter
from app.agents.questions import QUESTION_PROTOCOL_INSTRUCTIONS, extract_questions
from app.db import get_db
from tests.test_codex_adapter import (
    FakeExecutionProvider,
    _make_project,
    _task_complete_line,
    _thread_started_line,
)

BLOCK = """```agentflow-question
{"header": "Database", "question": "Which database?",
 "options": [{"label": "Postgres", "description": "Robust"}, "SQLite"]}
```"""


def test_extracts_block_and_keeps_surrounding_prose():
    text = f"I need a decision.\n\n{BLOCK}\n"
    prose, specs = extract_questions(text)
    assert prose == "I need a decision."
    assert specs == [
        {
            "question": "Which database?",
            "header": "Database",
            "multi_select": False,
            "options": [
                {"label": "Postgres", "description": "Robust"},
                {"label": "SQLite", "description": ""},
            ],
        }
    ]


def test_accepts_list_of_questions():
    payload = json.dumps([{"question": "A?", "options": ["x"]}, {"question": "B?", "options": ["y"]}])
    prose, specs = extract_questions(f"```agentflow-question\n{payload}\n```")
    assert prose == "" and [s["question"] for s in specs] == ["A?", "B?"]


def test_ordinary_text_is_never_treated_as_a_question():
    for text in [
        "Which database would you prefer? Options: Postgres, SQLite. Please choose one.",
        "```python\nprint('hi')\n```",
        "1. Postgres\n2. SQLite",
    ]:
        assert extract_questions(text) == (text.strip(), [])


def test_malformed_blocks_are_left_untouched():
    for body in [
        "not json",
        '{"question": "Q?", "options": []}',
        '{"question": "", "options": ["a"]}',
        '{"options": ["a"]}',
        '{"question": "Q?", "options": "a"}',
        "[1, 2]",
    ]:
        text = f"```agentflow-question\n{body}\n```"
        assert extract_questions(text) == (text, []), body


def test_one_bad_block_does_not_hide_a_good_one():
    bad = "```agentflow-question\nnope\n```"
    prose, specs = extract_questions(f"{bad}\n{BLOCK}")
    assert prose == bad and len(specs) == 1


def test_protocol_instructions_teach_a_parseable_block():
    start = QUESTION_PROTOCOL_INSTRUCTIONS.index("```agentflow-question")
    example = QUESTION_PROTOCOL_INSTRUCTIONS[start:].split("\n\n")[0]
    # The fragment's own example must be recognised as a question.
    _, specs = extract_questions(example)
    assert len(specs) == 1 and len(specs[0]["options"]) == 2


def _start(app, message):
    db = get_db()
    project_id, working_directory = _make_project(db, app)
    provider = FakeExecutionProvider()
    provider.queue_turn([_thread_started_line("ext-q"), *_task_complete_line(message)])
    adapter = CodexAdapter(db, provider)
    context = AgentContext(
        project_id=project_id,
        working_directory=working_directory,
        execution_provider="host",
        execution_target="1",
    )
    return db, adapter.start(context, "plan it")


def test_codex_reply_with_block_becomes_question_event(app):
    with app.app_context():
        db, session = _start(app, f"Need input.\n{BLOCK}")

        events = agent_models.list_agent_events(db, session.id)
        assert [e.event_type for e in events] == [
            "PromptSubmitted",
            "AgentText",
            "ClarifyingQuestion",
            "AgentComplete",
        ]
        assert events[1].data == "Need input."
        assert events[3].data == "Need input."
        (q,) = agent_models.list_clarifying_questions(db, session.id, status="PENDING")
        assert q.question == "Which database?" and q.header == "Database"


def test_codex_question_only_reply_emits_no_empty_text(app):
    with app.app_context():
        db, session = _start(app, BLOCK)
        types = [e.event_type for e in agent_models.list_agent_events(db, session.id)]
        assert types == ["PromptSubmitted", "ClarifyingQuestion", "AgentComplete"]


def test_codex_plain_and_malformed_replies_unchanged(app):
    with app.app_context():
        text = "All done.\n```agentflow-question\nnot json\n```"
        db, session = _start(app, text)
        events = agent_models.list_agent_events(db, session.id)
        assert [e.event_type for e in events] == ["PromptSubmitted", "AgentText", "AgentComplete"]
        assert events[1].data == text
        assert agent_models.list_clarifying_questions(db, session.id) == []
