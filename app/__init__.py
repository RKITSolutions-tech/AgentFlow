from flask import Flask

from app.config import Config
from app.db import close_db, init_db


def create_app(config: Config | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config or Config.from_env())

    init_db(app)
    app.teardown_appcontext(close_db)

    from app.projects.routes import bp as projects_bp
    from app.workspace.routes import bp as workspace_bp
    from app.api import bp as api_bp
    from app.sessions.routes import bp as sessions_bp
    from app.settings.routes import bp as settings_bp
    from app.notifications.routes import bp as notifications_bp

    app.register_blueprint(projects_bp)
    app.register_blueprint(workspace_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(notifications_bp)

    from app.runs.executor import RunManager
    from app.runs.routes import bp as runs_bp

    app.extensions["run_manager"] = RunManager(app.config)
    app.register_blueprint(runs_bp)

    from app.backlog.views import bp as backlog_bp

    app.register_blueprint(backlog_bp)

    from app.sprints.views import bp as sprints_bp

    app.register_blueprint(sprints_bp)

    from app.pipelines.views import bp as pipelines_bp
    from app.ralph.views import bp as ralph_bp

    app.register_blueprint(pipelines_bp)
    app.register_blueprint(ralph_bp)

    from app.artifacts.views import bp as artifacts_bp

    app.register_blueprint(artifacts_bp)

    from app.acceptance.views import bp as acceptance_bp

    app.register_blueprint(acceptance_bp)
    app.extensions["run_manager"].reconcile()

    from app.pipelines.persistence import seed_builtins

    with app.app_context():
        from app.db import get_db

        seed_builtins(get_db())

    from app.pipelines.manager import PipelineManager

    app.extensions["pipeline_manager"] = PipelineManager(app.config)

    from app.ralph.manager import RalphManager

    app.extensions["ralph_manager"] = RalphManager(app.extensions["pipeline_manager"])
    app.extensions["ralph_manager"].reconcile()

    from app.shell import init_shell

    init_shell(app)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index():
        from flask import redirect, url_for

        return redirect(url_for("projects.list_projects"))

    return app
