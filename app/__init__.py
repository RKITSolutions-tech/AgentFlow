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

    app.register_blueprint(projects_bp)
    app.register_blueprint(workspace_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(sessions_bp)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index():
        from flask import redirect, url_for

        return redirect(url_for("projects.list_projects"))

    return app
