"""Regression suite for task 71 (docs/SESSION_HISTORY_AND_CHAT_CONTEXT.md §7):
chat messages, the chat-history page and exports must never expose a secret that
`app.runs.security.redact` would catch. Redaction itself happens once, centrally,
in `app.agents.models.add_agent_event` (task 61) -- these tests exercise that
chokepoint through the chat surface, not `redact()` in isolation (already covered
by tests/test_runs.py::test_redact_masks_secrets)."""

import pytest

from app.agents.base import AgentContext
from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.sessions import chat, export

SECRETS = [
    "API_KEY=abcd1234efgh",
    'export TOKEN="s3cr3t value"',
    "Authorization: Bearer abcdef1234567890",
    '{"password": "hunter2"}',
    "key sk-abcdefghijklmnopqrstuvwx",
    "ghp_" + "a" * 30,
    "AKIAABCDEFGHIJKLMNOP",
    "postgres://user:pa55word@host/db",
]
_LEAKS = ("abcd1234efgh", "s3cr3t", "abcdef1234567890", "hunter2", "pa55word", "AKIAABC")


@pytest.fixture
def db(app):
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO projects (name, slug) VALUES ('P', 'p')")
        conn.commit()
        yield conn


def _start_with_text(db, text):
    adapter = FakeAgentAdapter(db)
    return adapter.start(
        AgentContext(project_id=1, working_directory="."), "hi",
        options={"role": "GENERAL", "script": [{"action": "message", "text": text}]},
    ).id


@pytest.mark.parametrize("secret_text", SECRETS)
def test_secret_in_agent_message_is_redacted_in_chat(db, secret_text):
    session_id = _start_with_text(db, secret_text)
    messages = chat.get_messages_by_session(db, session_id)
    agent_message = next(m for m in messages if m.role == "agent")
    assert agent_message.redacted is True
    for leaked in _LEAKS:
        assert leaked not in agent_message.content


@pytest.mark.parametrize("secret_text", SECRETS)
def test_secret_in_developer_prompt_is_redacted_in_chat(db, secret_text):
    adapter = FakeAgentAdapter(db)
    session = adapter.start(
        AgentContext(project_id=1, working_directory="."), secret_text,
        options={"role": "GENERAL", "script": []},
    )
    messages = chat.get_messages_by_session(db, session.id)
    developer_message = next(m for m in messages if m.role == "developer")
    assert developer_message.redacted is True
    for leaked in _LEAKS:
        assert leaked not in developer_message.content


def test_custom_redact_pattern_from_env_applies_to_chat(db, monkeypatch):
    monkeypatch.setenv("AGENTFLOW_REDACT_PATTERNS", '["ACME-\\\\d+"]')
    session_id = _start_with_text(db, "ticket ACME-1234 needs a fix")
    messages = chat.get_messages_by_session(db, session_id)
    agent_message = next(m for m in messages if m.role == "agent")
    assert agent_message.redacted is True
    assert "ACME-1234" not in agent_message.content


@pytest.mark.parametrize("secret_text", SECRETS)
def test_chat_history_page_never_shows_unredacted_secret(client, app, secret_text):
    resp = client.post("/projects/new", data={"name": "RedactProj", "description": ""})
    project_id = int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    with app.app_context():
        db = get_db()
        _start_with_text(db, secret_text)

    resp = client.get(f"/projects/{project_id}/chat-history")
    for leaked in _LEAKS:
        assert leaked.encode() not in resp.data

    resp = client.get(f"/projects/{project_id}/chat/export?format=markdown")
    for leaked in _LEAKS:
        assert leaked.encode() not in resp.data

    resp = client.get(f"/projects/{project_id}/chat/export?format=json")
    for leaked in _LEAKS:
        assert leaked.encode() not in resp.data


def test_warm_start_context_never_carries_an_unredacted_secret(db):
    _start_with_text(db, "api_key=sk-abcdefghijklmnopqrst")
    context = chat.get_session_context(db, project_id=1)
    assert "sk-abcdefghijklmnopqrst" not in context
    assert "[REDACTED]" in context
