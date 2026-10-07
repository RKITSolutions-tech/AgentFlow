import pytest

from app.agents.base import AgentContext
from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.sessions import chat


@pytest.fixture
def db(app):
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        conn.commit()
        yield conn


def _start(db, role="GENERAL", script=None):
    adapter = FakeAgentAdapter(db)
    session = adapter.start(
        AgentContext(project_id=1, working_directory="."),
        "hello agent",
        options={"role": role, "script": script or []},
    )
    return adapter, session.id


def test_general_session_messages_are_chat(db):
    _, session_id = _start(db, script=[{"action": "message", "text": "hi there"}])
    messages = chat.get_messages_by_session(db, session_id)
    assert [m.role for m in messages] == ["developer", "agent"]
    assert messages[0].content == "hello agent"
    assert messages[1].content == "hi there"
    assert all(not m.redacted for m in messages)


def test_non_general_sessions_are_excluded(db):
    for role in ("IMPLEMENTATION", "PLANNING", "RESEARCH"):
        _start(db, role=role, script=[{"action": "message", "text": "operational chatter"}])
    assert chat.get_chat_history(db, project_id=1) == []


def test_agent_complete_and_status_are_not_chat_messages(db):
    adapter, session_id = _start(db, script=[{"action": "complete"}])
    adapter.stop(session_id)
    messages = chat.get_messages_by_session(db, session_id)
    # Only the developer's initial PromptSubmitted survives; AgentComplete (duplicates
    # the preceding AgentText) and AgentStatus (bookkeeping) are not chat content.
    assert [m.message_type for m in messages] == ["text"]
    assert [m.role for m in messages] == ["developer"]


def test_clarifying_question_renders_as_ask(db):
    _, session_id = _start(
        db,
        script=[{"action": "ask", "question": "Which auth flow?", "options": ["JWT", "Session"]}],
    )
    messages = chat.get_messages_by_session(db, session_id)
    ask = [m for m in messages if m.message_type == "ask"]
    assert len(ask) == 1
    assert ask[0].content == "Which auth flow?"
    assert ask[0].question["options"][0]["label"] == "JWT"


def test_secrets_are_redacted_before_storage(db):
    _, session_id = _start(
        db, script=[{"action": "message", "text": "api_key=sk-abcdefghijklmnopqrst"}]
    )
    messages = chat.get_messages_by_session(db, session_id)
    agent_message = [m for m in messages if m.role == "agent"][0]
    assert "sk-abcdefghijklmnopqrst" not in agent_message.content
    assert agent_message.redacted is True


def test_get_recent_messages_respects_window_and_limit(db):
    _start(db, script=[{"action": "message", "text": f"msg {i}"} for i in range(3)])
    recent = chat.get_recent_messages(db, project_id=1, limit=2, hours=24)
    assert len(recent) == 2
    # oldest-first for display
    assert recent[0].created_at <= recent[1].created_at


def test_search_messages_matches_content(db):
    _start(db, script=[{"action": "message", "text": "clarify the auth flow"}])
    _start(db, script=[{"action": "message", "text": "unrelated topic"}])
    results = chat.search_messages(db, project_id=1, query="auth flow")
    assert len(results) == 1
    assert "auth flow" in results[0].content


def test_get_chat_history_filters_by_role_and_session(db):
    _, session_a = _start(db, script=[{"action": "message", "text": "from a"}])
    _, session_b = _start(db, script=[{"action": "message", "text": "from b"}])
    only_a = chat.get_chat_history(db, project_id=1, session_id=session_a)
    assert {m.session_id for m in only_a} == {session_a}
    developer_only = chat.get_chat_history(db, project_id=1, role="developer")
    assert all(m.role == "developer" for m in developer_only)
    assert {m.session_id for m in developer_only} == {session_a, session_b}


def test_get_session_context_formats_recent_messages(db):
    _start(db, script=[{"action": "message", "text": "hi there"}])
    context = chat.get_session_context(db, project_id=1)
    assert context.startswith("[System Context]")
    assert "hi there" in context
    assert "hello agent" in context


def test_get_session_context_empty_when_no_history(db):
    assert chat.get_session_context(db, project_id=1) == ""


def test_export_chat_history_markdown_and_json(db):
    from app.sessions import export

    _start(db, script=[{"action": "message", "text": "hi there, api_key=sk-abcdefghijklmnopqrst"}])

    markdown = export.export_chat_history(db, project_id=1, format="markdown")
    assert "hi there" in markdown
    assert "sk-abcdefghijklmnopqrst" not in markdown
    assert "[REDACTED]" in markdown

    import json as json_module

    payload = json_module.loads(export.export_chat_history(db, project_id=1, format="json"))
    assert payload["message_count"] == 2
    assert all("sk-abcdefghijklmnopqrst" not in m["content"] for m in payload["messages"])

    import pytest as _pytest
    with _pytest.raises(ValueError):
        export.export_chat_history(db, project_id=1, format="xml")


def test_summary_crud(db):
    summary_id = chat.create_summary(
        db, project_id=1, period_start="2026-10-01 00:00:00", period_end="2026-10-02 00:00:00",
        summary="session: clarified auth flow", message_count=4, session_ids=[1, 2],
    )
    summary = chat.get_summary(db, summary_id)
    assert summary.summary == "session: clarified auth flow"
    assert summary.session_ids == [1, 2]
    assert [s.id for s in chat.list_summaries_for_project(db, 1)] == [summary_id]
