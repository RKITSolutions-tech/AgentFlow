"""Task 34: project context files, per-project overrides, usage counts,
recorded-prompt browser and import/export."""
import json

import pytest

from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.execution.host import HostExecutionProvider
from app.pipelines import executions, persistence
from app.pipelines.engine import PipelineEngine
from app.projects import models as project_models
from app.prompts import assembler, models
from app.prompts.models import LibraryError
from app.ralph import models as ralph
from app.ralph.orchestrator import RalphOrchestrator
from tests.conftest import create_project_with_repo

AJAX = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def env(app, client, tmp_path):
    project_id, repo = create_project_with_repo(client, app.config["allowed_root"])
    with app.app_context():
        db = get_db()
        provider = HostExecutionProvider(app.config["DATABASE_PATH"], app.config["ALLOWED_PROJECT_ROOTS"])
        factory = lambda c: FakeAgentAdapter(c)
        engine = PipelineEngine(db, provider, str(tmp_path / "artifacts"), factory, sleep=lambda s: None)
        orch = RalphOrchestrator(db, provider, engine, factory)
        yield type("E", (), {"db": db, "engine": engine, "orch": orch, "pid": project_id, "repo": repo})


def _agent_step(env, **config):
    persistence.create_pipeline(
        env.db, {"name": "p", "elements": [{"name": "agent", "type": "AGENT", "config": {
            "script": [{"action": "message", "text": "ok"}, {"action": "complete"}], **config}}]}, env.pid)
    eid = env.engine.create("p", env.pid, 1, variables={"task": "Fix"})
    env.engine.run(eid)
    return eid


# -- context files ---------------------------------------------------------------------------


def test_discovery_prefers_claude_md_then_agents_md(env):
    assert assembler.project_context(env.db, env.pid)[1] == []
    (open(f"{env.repo}/AGENTS.md", "w")).write("agents rules")
    assert assembler.project_context(env.db, env.pid)[1] == ["AGENTS.md"]
    (open(f"{env.repo}/CLAUDE.md", "w")).write("claude rules")
    assert assembler.project_context(env.db, env.pid)[1] == ["CLAUDE.md"]


def test_explicit_setting_replaces_discovery_and_is_validated(env):
    open(f"{env.repo}/CLAUDE.md", "w").write("c")
    open(f"{env.repo}/NOTES.md", "w").write("n")
    project_models.set_context_files(env.db, env.pid, "NOTES.md\n\n")
    assert assembler.project_context(env.db, env.pid)[1] == ["NOTES.md"]
    for bad in ("../secret", "/etc/passwd"):
        with pytest.raises(ValueError):
            project_models.set_context_files(env.db, env.pid, bad)
    assert project_models.get_project(env.db, env.pid).context_files == "NOTES.md"


def test_pipeline_step_prompt_includes_project_context(env):
    open(f"{env.repo}/CLAUDE.md", "w").write("ALWAYS USE TABS")
    eid = _agent_step(env, prompt="Do it")
    rec = models.get_execution_prompt(env.db, executions.list_steps(env.db, eid)[0].execution_prompt_id)
    assert "ALWAYS USE TABS" in rec.effective_prompt and rec.context_files == ["CLAUDE.md"]


def test_ralph_iteration_prompt_includes_project_context(env):
    open(f"{env.repo}/AGENTS.md", "w").write("RUN THE LINTER")
    run = ralph.get_run(env.db, ralph.create_run(env.db, env.pid, 1, "T", "do it", "verify"))
    prompt = env.orch._build_prompt(run, 1, None, [])
    assert "RUN THE LINTER" in prompt and env.orch._context_used == ["AGENTS.md"]


# -- overrides ---------------------------------------------------------------------------------


def test_template_override_applies_only_to_that_project(env):
    t = models.get_template_by_name(env.db, "implement-task")
    models.set_override(env.db, env.pid, "template", t.id, "PROJECT BODY ${vars.task}")
    assert assembler.assemble_effective_prompt(env.db, t, variables={"vars.task": "x"}, project_id=env.pid).text.startswith("PROJECT BODY")
    assert "PROJECT BODY" not in assembler.assemble_effective_prompt(env.db, t).text
    eid = _agent_step(env, prompt_template="implement-task")
    assert executions.list_steps(env.db, eid)[0].input_reference == "PROJECT BODY Fix"
    models.clear_override(env.db, env.pid, "template", t.id)
    assert "PROJECT BODY" not in assembler.assemble_effective_prompt(env.db, t, project_id=env.pid).text


def test_block_override_changes_text_and_state(env):
    blocks = models.list_blocks(env.db)
    models.set_override(env.db, env.pid, "block", blocks[0].id, enabled=False)
    models.set_override(env.db, env.pid, "block", blocks[1].id, content="Only in this project")
    run = ralph.get_run(env.db, ralph.create_run(env.db, env.pid, 1, "T", "do it", "verify"))
    prompt = env.orch._build_prompt(run, 1, None, [])
    assert "Only in this project" in prompt and blocks[0].content not in prompt
    assert blocks[0].name not in env.orch._blocks_used
    assert models.list_blocks(env.db)[0].enabled and blocks[1].content != "Only in this project"  # global untouched


def test_override_validation(env):
    t = models.get_template_by_name(env.db, "implement-task")
    for args in (("template", t.id, ""), ("template", 9999, "x"), ("block", 9999, "x"), ("nope", 1, "x")):
        with pytest.raises(LibraryError):
            models.set_override(env.db, env.pid, *args)
    with pytest.raises(LibraryError):
        models.set_override(env.db, env.pid, "block", models.list_blocks(env.db)[0].id)  # nothing changed


# -- usage, recorded browser ---------------------------------------------------------------------


def test_usage_counts_and_recorded_browser(env):
    t = models.get_template_by_name(env.db, "implement-task")
    _agent_step(env, prompt_template="implement-task")
    run = ralph.get_run(env.db, ralph.create_run(env.db, env.pid, 1, "Ralph T", "do it", "verify"))
    prompt = env.orch._build_prompt(run, 1, None, [])
    it = ralph.add_iteration(env.db, run.id, 1, prompt, False)
    models.record_prompt(env.db, "ralph_iteration", it, prompt, blocks_included=env.orch._blocks_used)
    assert models.template_usage(env.db)[t.id] == 1
    assert models.block_usage(env.db)[env.orch._blocks_used[0]] == 1

    rows = models.list_recorded(env.db, env.pid)
    assert {r["source_type"] for r in rows} == {"pipeline_step", "ralph_iteration"}
    assert [r["source_type"] for r in models.list_recorded(env.db, env.pid, "ralph_iteration")] == ["ralph_iteration"]
    assert models.list_recorded(env.db, env.pid + 1) == []
    assert models.get_recorded_for_project(env.db, env.pid + 1, rows[0]["id"]) is None


def test_project_pages_and_endpoints(client, env):
    base = f"/prompts/projects/{env.pid}"
    _agent_step(env, prompt="Hello ${vars.task}")
    page = client.get(base).get_data(as_text=True)
    assert "Context files" in page and "Execution #" in page and "Recorded prompts" in page
    assert client.get("/prompts/projects/9999").status_code == 404

    open(f"{env.repo}/CLAUDE.md", "w").write("x")
    assert client.post(f"{base}/context", data={"context_files": "CLAUDE.md"}, headers=AJAX).status_code == 200
    assert client.post(f"{base}/context", data={"context_files": "../x"}, headers=AJAX).status_code == 400

    t = models.get_template_by_name(env.db, "implement-task")
    b = models.list_blocks(env.db)[0]
    assert client.post(f"{base}/overrides/template/{t.id}", data={"content": "mine"}, headers=AJAX).status_code == 200
    assert client.post(f"{base}/overrides/block/{b.id}", data={"enabled": "0"}, headers=AJAX).status_code == 200
    assert client.post(f"{base}/overrides/template/{t.id}", data={"content": ""}, headers=AJAX).status_code == 400
    assert "(overridden" in client.get(base).get_data(as_text=True)
    assert client.post(f"{base}/overrides/block/{b.id}/delete", headers=AJAX).status_code == 200
    assert models.get_overrides(env.db, env.pid, "block") == {}

    prompt_id = models.list_recorded(env.db, env.pid)[0]["id"]
    detail = client.get(f"{base}/recorded/{prompt_id}").get_data(as_text=True)
    assert "Hello Fix" in detail
    assert client.get(f"{base}/recorded/99999").status_code == 404
    assert client.get(f"/prompts/projects/{env.pid + 1}/recorded/{prompt_id}").status_code == 404


def test_library_page_shows_usage_and_project_tab_links(client, env):
    _agent_step(env, prompt_template="implement-task")
    assert "<th>Used</th>" in client.get("/prompts/library").get_data(as_text=True)
    assert f"/prompts/projects/{env.pid}" in client.get(f"/projects/{env.pid}").get_data(as_text=True)
    assert "used " in client.get("/prompts/ralph-blocks").get_data(as_text=True)


def test_preview_honours_project_overrides(client, env):
    t = models.get_template_by_name(env.db, "implement-task")
    models.set_override(env.db, env.pid, "template", t.id, "PREVIEW OVERRIDE")
    out = client.post("/prompts/preview", data={"template_id": t.id, "project_id": env.pid}, headers=AJAX).get_json()
    assert "PREVIEW OVERRIDE" in out["prompt"]


# -- import / export -------------------------------------------------------------------------------


def test_export_import_round_trip_and_merge_rules(client, app, env):
    frag = models.create_fragment(env.db, "rules", "Be kind", tags=["x"])
    base = models.create_template(env.db, "base", body="BASE")
    models.create_template(env.db, "child", fragments=[frag], base_template_id=base, variables={"a": "1"})
    exported = client.get("/prompts/export")
    assert exported.status_code == 200 and "attachment" in exported.headers["Content-Disposition"]
    data = json.loads(exported.get_data(as_text=True))
    assert next(t for t in data["templates"] if t["name"] == "child")["base"] == "base"

    # Into an empty library everything is created, parents before children.
    for table in ("prompt_templates", "prompt_fragments", "ralph_instruction_blocks"):
        env.db.execute(f"DELETE FROM {table}")
    env.db.commit()
    result = models.import_library(env.db, data)
    assert result["errors"] == [] and result["created"] == len(data["fragments"]) + len(data["templates"]) + len(data["blocks"])
    child = models.get_template_by_name(env.db, "child")
    assert child.base_template_id == models.get_template_by_name(env.db, "base").id and child.variables == {"a": "1"}

    # Existing names are kept unless overwrite.
    data["fragments"] = [{"name": "rules", "content": "Changed", "category": "instruction", "tags": []}]
    data["templates"], data["blocks"] = [], []
    assert models.import_library(env.db, data)["skipped"] == 1
    assert models.get_fragment_by_name(env.db, "rules").content == "Be kind"
    assert models.import_library(env.db, data, overwrite=True)["updated"] == 1
    assert models.get_fragment_by_name(env.db, "rules").content == "Changed"


def test_import_rejects_bad_files_and_reports_bad_items(client, env):
    for bad in ("not json", json.dumps({"format": "other"}), json.dumps({"format": "agentflow-prompt-library", "version": 9})):
        resp = client.post("/prompts/import", data={"json": bad}, headers=AJAX)
        assert resp.status_code == 400 and "Import failed" in resp.get_json()["error"]
    data = {"format": "agentflow-prompt-library", "version": 1,
            "templates": [{"name": "orphan", "body": "x", "base": "missing"},
                          {"name": "needs-frag", "body": "x", "fragments": ["nope"]}]}
    result = models.import_library(env.db, data)
    assert result["created"] == 0 and len(result["errors"]) == 2
    upload = client.post("/prompts/import", data={"json": json.dumps({
        "format": "agentflow-prompt-library", "version": 1, "fragments": [{"name": "up", "content": "c"}]})}, headers=AJAX)
    assert upload.status_code == 200 and models.get_fragment_by_name(env.db, "up") is not None
