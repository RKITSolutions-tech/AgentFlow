"""Coverage for AGENTFLOW_PLANNING_AGENT's four values (task 55): the codex/fake
branch already covered indirectly via PlanningAgent itself (test_sprint_models.py)
gains `claude` and `local`, both wired through `build_planning_agent`/
`build_context` in app/sprints/planning_agent.py."""
from pathlib import Path

import pytest

from app.agents.claude import ClaudeAdapter
from app.agents.codex import CodexAdapter
from app.agents.fake import FakeAgentAdapter
from app.db import get_db
from app.projects import models as project_models
from app.settings import models as settings_models
from app.settings.models import ModelCatalogConfigError
from app.sprints.planning_agent import build_context, build_planning_agent


def _make_project(db, app) -> tuple[int, str]:
    working_directory = str(Path(app.config["allowed_root"]) / "repo")
    Path(working_directory).mkdir(parents=True, exist_ok=True)
    project_id = project_models.create_project(db, "Planning Project")
    project_models.add_repository(
        db, project_id, "repo", working_directory, app.config["ALLOWED_PROJECT_ROOTS"], is_primary=True,
    )
    return project_id, working_directory


def test_codex_is_still_the_default(app):
    with app.app_context():
        db = get_db()
        agent = build_planning_agent(app.config, db)
        assert isinstance(agent._adapter, CodexAdapter)


def test_fake_still_works(app):
    app.config["PLANNING_AGENT"] = "fake"
    with app.app_context():
        db = get_db()
        agent = build_planning_agent(app.config, db)
        assert isinstance(agent._adapter, FakeAgentAdapter)
        assert agent._scripted is True

        project_id, working_directory = _make_project(db, app)
        context = build_context(app.config, project_id, working_directory, db)
        assert context.execution_provider == "host"
        assert context.execution_target == ""
        assert context.model is None


def test_claude_instantiates_claude_adapter(app):
    app.config["PLANNING_AGENT"] = "claude"
    with app.app_context():
        db = get_db()
        agent = build_planning_agent(app.config, db)
        assert isinstance(agent._adapter, ClaudeAdapter)

        project_id, working_directory = _make_project(db, app)
        context = build_context(app.config, project_id, working_directory, db)
        assert context.execution_provider == "host"
        assert context.model is None  # no catalog model named: plain `claude` session


def test_local_with_claude_style_catalog_entry_instantiates_claude_adapter(app):
    app.config["PLANNING_AGENT"] = "local"
    app.config["PLANNING_LOCAL_ADAPTER"] = "claude"
    app.config["PLANNING_MODEL"] = "self-hosted-anthropic"
    with app.app_context():
        db = get_db()
        settings_models.add_model(
            db, "local", "self-hosted-anthropic",
            base_url="http://localhost:8080", api_key="anthropic-key",
        )

        agent = build_planning_agent(app.config, db)
        assert isinstance(agent._adapter, ClaudeAdapter)

        project_id, working_directory = _make_project(db, app)
        context = build_context(app.config, project_id, working_directory, db)
        assert context.model == "self-hosted-anthropic"

        # The resolved model, threaded onto the context, is exactly what makes
        # ClaudeAdapter apply the catalog entry's base_url/api_key (already
        # covered end-to-end in tests/test_claude_adapter.py); confirm the
        # same lookup used at session-start time resolves here too.
        env = agent._adapter._local_model_env(context.model)
        assert env == {
            "ANTHROPIC_BASE_URL": "http://localhost:8080",
            "ANTHROPIC_AUTH_TOKEN": "anthropic-key",
        }


def test_local_with_codex_style_catalog_entry_instantiates_codex_adapter(app):
    app.config["PLANNING_AGENT"] = "local"
    app.config["PLANNING_LOCAL_ADAPTER"] = "codex"
    app.config["PLANNING_MODEL"] = "self-hosted-openai"
    with app.app_context():
        db = get_db()
        settings_models.add_model(
            db, "local", "self-hosted-openai",
            base_url="http://localhost:1234/v1", api_key="openai-key",
        )

        agent = build_planning_agent(app.config, db)
        assert isinstance(agent._adapter, CodexAdapter)

        project_id, working_directory = _make_project(db, app)
        context = build_context(app.config, project_id, working_directory, db)
        assert context.model == "self-hosted-openai"

        flags = agent._adapter._model_flags(context.model)
        assert flags == [
            "-m", "self-hosted-openai",
            "-c", 'model_provider="local"',
            "-c", 'model_providers.local.name="Local"',
            "-c", 'model_providers.local.base_url="http://localhost:1234/v1"',
            "-c", 'model_providers.local.wire_api="chat"',
            "-c", 'model_providers.local.env_key="AGENTFLOW_LOCAL_MODEL_API_KEY"',
        ]


def test_local_defaults_to_codex_style_adapter(app):
    app.config["PLANNING_AGENT"] = "local"
    app.config["PLANNING_MODEL"] = "self-hosted-openai"
    with app.app_context():
        db = get_db()
        settings_models.add_model(db, "local", "self-hosted-openai", base_url="http://localhost:1234/v1")
        agent = build_planning_agent(app.config, db)
        assert isinstance(agent._adapter, CodexAdapter)


def test_local_without_planning_model_raises_config_error(app):
    app.config["PLANNING_AGENT"] = "local"
    with app.app_context():
        db = get_db()
        with pytest.raises(ModelCatalogConfigError, match="AGENTFLOW_PLANNING_MODEL"):
            build_planning_agent(app.config, db)


def test_local_with_unknown_model_id_raises_config_error(app):
    app.config["PLANNING_AGENT"] = "local"
    app.config["PLANNING_MODEL"] = "does-not-exist"
    with app.app_context():
        db = get_db()
        with pytest.raises(ModelCatalogConfigError, match="does-not-exist"):
            build_planning_agent(app.config, db)


def test_local_with_disabled_model_raises_config_error(app):
    app.config["PLANNING_AGENT"] = "local"
    app.config["PLANNING_MODEL"] = "disabled-model"
    with app.app_context():
        db = get_db()
        catalog_id = settings_models.add_model(
            db, "local", "disabled-model", base_url="http://localhost:9999"
        )
        settings_models.set_model_enabled(db, catalog_id, False)
        with pytest.raises(ModelCatalogConfigError):
            build_planning_agent(app.config, db)


def test_build_context_also_raises_for_local_misconfiguration(app):
    """build_context is called separately from build_planning_agent
    (app/sprints/views.py's `_context`/`_agent` helpers); both must fail the
    same way rather than one silently defaulting."""
    app.config["PLANNING_AGENT"] = "local"
    with app.app_context():
        db = get_db()
        project_id, working_directory = _make_project(db, app)
        with pytest.raises(ModelCatalogConfigError):
            build_context(app.config, project_id, working_directory, db)
