import sys

import pytest

from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.pipelines import executions, persistence
from app.pipelines.engine import PipelineEngine
from app.prompts import models
from tests.conftest import create_project_with_repo, launch_chromium, require_playwright

AJAX = {"X-Requested-With": "XMLHttpRequest"}


def test_library_lists_seeded_and_creates_fragment_and_template(client, app):
    page = client.get("/prompts/library").get_data(as_text=True)
    assert "implement-task" in page and "Prompt library" in page
    resp = client.post("/prompts/library", data={"name": "tone", "content": "Be brief", "category": "instruction", "tags": "style"}, headers=AJAX)
    assert resp.status_code == 200
    assert client.post("/prompts/library", data={"name": "tone", "content": "dup"}, headers=AJAX).status_code == 400
    with app.app_context():
        fid = models.get_fragment_by_name(get_db(), "tone").id
    resp = client.post("/prompts/templates", data={"name": "mine", "body": "Do ${vars.task}", "fragments": [fid]}, headers=AJAX)
    assert resp.status_code == 200 and "/prompts/templates/" in resp.get_json()["redirect"]
    assert "mine" in client.get("/prompts/library").get_data(as_text=True)
    assert client.post("/prompts/templates", data={"name": "empty"}, headers=AJAX).status_code == 400
    assert client.get("/prompts/templates").status_code == 200


def test_fragment_edit_and_delete_over_ajax(client, app):
    client.post("/prompts/library", data={"name": "f", "content": "v1"}, headers=AJAX)
    with app.app_context():
        fid = models.get_fragment_by_name(get_db(), "f").id
    page = client.get(f"/prompts/fragments/{fid}").get_data(as_text=True)
    assert "v1" in page and "History" in page
    assert client.post(f"/prompts/fragments/{fid}", data={"content": "v2"}, headers=AJAX).status_code == 200
    with app.app_context():
        assert models.get_fragment(get_db(), fid).version == 2
    resp = client.post(f"/prompts/fragments/{fid}/delete", headers=AJAX)
    assert resp.status_code == 200
    assert client.post(f"/prompts/fragments/{fid}/delete", headers=AJAX).status_code == 404


def test_fragment_in_use_cannot_be_deleted(client, app):
    client.post("/prompts/library", data={"name": "f", "content": "x"}, headers=AJAX)
    with app.app_context():
        fid = models.get_fragment_by_name(get_db(), "f").id
    client.post("/prompts/templates", data={"name": "t", "fragments": [fid]}, headers=AJAX)
    resp = client.post(f"/prompts/fragments/{fid}/delete", headers=AJAX)
    assert resp.status_code == 409 and "In use" in resp.get_json()["error"]


def test_template_edit_and_delete(client, app):
    resp = client.post("/prompts/templates", data={"name": "t", "body": "one"}, headers=AJAX)
    url = resp.get_json()["redirect"]
    tid = int(url.rsplit("/", 1)[1])
    assert client.post(url, data={"name": "t2", "body": "two", "variables": "a=1\nb = 2"}, headers=AJAX).status_code == 200
    with app.app_context():
        t = models.get_template(get_db(), tid)
    assert (t.name, t.body, t.variables) == ("t2", "two", {"a": "1", "b": "2"})
    assert "two" in client.get(url).get_data(as_text=True)
    assert client.post(f"/prompts/templates/{tid}/delete", headers=AJAX).status_code == 200
    assert client.get(url).status_code == 404


def test_preview_saved_and_unsaved(client, app):
    with app.app_context():
        tid = models.get_template_by_name(get_db(), "implement-task").id
    saved = client.post("/prompts/preview", data={"template_id": tid, "preview_variables": "vars.task=Fix bug"}, headers=AJAX).get_json()
    assert "Fix bug" in saved["prompt"] and saved["metadata"]["template"] == "implement-task"
    unsaved = client.post("/prompts/preview", data={"body": "Hello ${who}"}, headers=AJAX).get_json()
    assert unsaved["prompt"] == "Hello ${who}" and unsaved["metadata"]["missing_variables"] == ["who"]
    ralph = client.post("/prompts/preview", data={"body": "B", "include_ralph": "on"}, headers=AJAX).get_json()
    assert "Instructions:" in ralph["prompt"] and ralph["metadata"]["blocks_included"]
    assert client.post("/prompts/preview", data={"body": " "}, headers=AJAX).status_code == 400
    assert client.post("/prompts/preview", data={"template_id": 9999}, headers=AJAX).status_code == 404


def test_preview_resolves_repository_files(client, app):
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with open(f"{repo}/notes.md", "w") as fh:
        fh.write("remember this")
    with app.app_context():
        repo_id = get_db().execute("SELECT id FROM repositories").fetchone()[0]
    out = client.post("/prompts/preview", data={
        "body": "See @notes.md", "project_id": project_id, "repository_id": repo_id,
    }, headers=AJAX).get_json()
    assert out["metadata"]["resolved_files"] == ["notes.md"] and "remember this" in out["prompt"]


def test_ralph_blocks_toggle_and_reorder_persist(client, app):
    page = client.get("/prompts/ralph-blocks").get_data(as_text=True)
    assert "keep-minimal" in page and 'aria-checked="true"' in page
    with app.app_context():
        blocks = models.list_blocks(get_db())
    first, second = blocks[0], blocks[1]
    assert client.post(f"/prompts/ralph-blocks/{first.id}", data={"enabled": "0"}, headers=AJAX).status_code == 200
    assert 'aria-checked="false"' in client.get("/prompts/ralph-blocks").get_data(as_text=True)
    assert client.post(f"/prompts/ralph-blocks/{second.id}", data={"move": "up"}, headers=AJAX).status_code == 200
    with app.app_context():
        db = get_db()
        assert models.list_blocks(db)[0].id == second.id and models.get_block(db, first.id).enabled is False
    assert client.post("/prompts/ralph-blocks", data={"name": "extra", "content": "Do X"}, headers=AJAX).status_code == 200
    assert client.post("/prompts/ralph-blocks", data={"name": "extra", "content": "dup"}, headers=AJAX).status_code == 400
    assert client.post(f"/prompts/ralph-blocks/{first.id}/delete", headers=AJAX).status_code == 200
    assert client.post("/prompts/ralph-blocks/9999", data={"enabled": "1"}, headers=AJAX).status_code == 404


# -- integration with the engine and Ralph -------------------------------------------------


@pytest.fixture
def engine_env(app, client, tmp_path):
    app.config["PLANNING_AGENT"] = "fake"
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        engine = PipelineEngine(db, provider, str(tmp_path / "artifacts"), lambda c: FakeAgentAdapter(c), sleep=lambda s: None)
        yield type("E", (), {"db": db, "engine": engine, "project_id": project_id, "repo": repo})


def _agent_pipeline(env, **config):
    persistence.create_pipeline(
        env.db, {"name": "p", "elements": [{"name": "agent", "type": "AGENT", "config": {"script": [{"action": "message", "text": "ok"}, {"action": "complete"}], **config}}]},
        env.project_id,
    )
    eid = env.engine.create("p", env.project_id, 1, variables={"task": "Fix the bug"})
    env.engine.run(eid)
    return eid


def test_pipeline_step_records_execution_prompt_from_library(engine_env):
    with open(f"{engine_env.repo}/ctx.txt", "w") as fh:
        fh.write("CONTEXT BODY")
    frag = models.create_fragment(engine_env.db, "rules", "Follow the rules.")
    models.create_template(engine_env.db, "custom", body="Task: ${vars.task}", fragments=[frag])
    eid = _agent_pipeline(engine_env, prompt_template="custom", context_files=["ctx.txt"])
    step = executions.list_steps(engine_env.db, eid)[0]
    assert step.status == "PASSED" and step.execution_prompt_id
    rec = models.get_execution_prompt(engine_env.db, step.execution_prompt_id)
    assert rec.source_type == "pipeline_step" and rec.source_id == step.id and rec.template_name == "custom"
    assert "Task: Fix the bug" in rec.effective_prompt and "Follow the rules." in rec.effective_prompt
    assert rec.context_files == ["ctx.txt"] and "CONTEXT BODY" in rec.effective_prompt
    assert rec.effective_prompt == step.input_reference  # what the agent got is what is on record


def test_library_edit_changes_the_prompt_the_engine_sends(engine_env):
    t = models.get_template_by_name(engine_env.db, "implement-task")
    models.update_template(engine_env.db, t.id, body="EDITED ${vars.task}")
    eid = _agent_pipeline(engine_env, prompt_template="implement-task")
    step = executions.list_steps(engine_env.db, eid)[0]
    assert step.input_reference == "EDITED Fix the bug"


def test_unseeded_library_falls_back_to_defaults_and_unknown_fails(engine_env):
    engine_env.db.execute("DELETE FROM prompt_templates")
    engine_env.db.commit()
    eid = _agent_pipeline(engine_env, prompt_template="implement-task")
    assert executions.list_steps(engine_env.db, eid)[0].status == "PASSED"
    eid = _agent_pipeline_named(engine_env, "q", prompt_template="no-such-template")
    step = executions.list_steps(engine_env.db, eid)[0]
    assert step.status == "FAILED" and "Unknown prompt template" in step.error_summary


def _agent_pipeline_named(env, name, **config):
    persistence.create_pipeline(
        env.db, {"name": name, "elements": [{"name": "agent", "type": "AGENT", "config": config}]}, env.project_id,
    )
    eid = env.engine.create(name, env.project_id, 1)
    env.engine.run(eid)
    return eid


def test_literal_prompt_is_still_supported_and_recorded(engine_env):
    eid = _agent_pipeline(engine_env, prompt="Say hi to ${vars.task}")
    step = executions.list_steps(engine_env.db, eid)[0]
    rec = models.get_execution_prompt(engine_env.db, step.execution_prompt_id)
    assert rec.effective_prompt == "Say hi to Fix the bug" and rec.template_id is None


def test_secrets_are_redacted_in_the_recorded_prompt(engine_env):
    engine_env.engine._patterns = (r"hunter2",)
    eid = _agent_pipeline(engine_env, prompt="password is hunter2")
    step = executions.list_steps(engine_env.db, eid)[0]
    rec = models.get_execution_prompt(engine_env.db, step.execution_prompt_id)
    assert "hunter2" not in rec.effective_prompt and rec.redacted


def test_ralph_prompt_uses_enabled_blocks_and_is_recorded(app, client, tmp_path):
    from app.ralph import models as ralph
    from app.ralph.orchestrator import RalphOrchestrator

    app.config["PLANNING_AGENT"] = "fake"
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        blocks = models.list_blocks(db)
        models.update_block(db, blocks[0].id, enabled=False)
        models.update_block(db, blocks[1].id, content="Custom instruction ONE")
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        factory = lambda c: FakeAgentAdapter(c)
        engine = PipelineEngine(db, provider, str(tmp_path / "a"), factory)
        orch = RalphOrchestrator(db, provider, engine, factory)
        run_id = ralph.create_run(db, project_id, 1, "T", "do it", "verify", acceptance=["a"])
        run = ralph.get_run(db, run_id)
        prompt = orch._build_prompt(run, 1, None, [])
        assert "Custom instruction ONE" in prompt and blocks[0].content not in prompt
        assert orch._blocks_used == [b.name for b in blocks[1:]]
        # All blocks disabled: really no instructions, not the defaults.
        for b in blocks:
            models.update_block(db, b.id, enabled=False)
        assert "Instructions:" not in orch._build_prompt(run, 1, None, [])
        # No blocks at all (never seeded): shipped defaults.
        db.execute("DELETE FROM ralph_instruction_blocks")
        db.commit()
        assert "Do not commit" in orch._build_prompt(run, 1, None, [])


# -- real browser ------------------------------------------------------------------------


@pytest.mark.parametrize("viewport", [{"width": 375, "height": 667}, {"width": 1280, "height": 800}], ids=["375", "1280"])
def test_library_pages_fit_the_viewport(app, client, live_server, viewport):
    sync_playwright = require_playwright()
    client.post("/prompts/library", data={"name": "long-" + "x" * 60, "content": "y" * 300}, headers=AJAX)
    with app.app_context():
        fid = models.get_fragment_by_name(get_db(), "long-" + "x" * 60).id
        tid = models.create_template(get_db(), "t", body="Body ${vars.task}")
    paths = ["/prompts/library", f"/prompts/fragments/{fid}", "/prompts/templates", f"/prompts/templates/{tid}", "/prompts/ralph-blocks"]
    with sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport=viewport)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for path in paths:
                page.goto(live_server + path)
                overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                assert overflow <= 0, f"{path} scrolls sideways by {overflow}px"
            page.goto(live_server + "/prompts/ralph-blocks")
            for toggle in page.locator(".block-toggle button").all():
                assert toggle.bounding_box()["height"] >= 40
            # The template form previews without saving.
            page.goto(f"{live_server}/prompts/templates/{tid}")
            page.fill("[data-preview-variables]", "vars.task=demo")
            page.click("[data-preview-url]")
            page.wait_for_function("document.querySelector('[data-preview-text]').textContent.includes('Body demo')")
            assert errors == []
        finally:
            browser.close()
