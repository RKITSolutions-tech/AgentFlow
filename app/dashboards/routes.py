from flask import Blueprint, render_template, abort

from app.db import get_db
from app.projects import models as project_models

bp = Blueprint(
    "dashboards",
    __name__,
    url_prefix="/dashboards",
)


@bp.route("/home")
def home():
    """Home dashboard with recent sessions, pipelines, and backlog items."""
    return render_template("dashboards/home.html")


@bp.route("/wikis")
def wikis():
    """Wikis dashboard with available wikis and recent docs."""
    return render_template("dashboards/wikis.html")


@bp.route("/project/<int:project_id>")
def project(project_id: int):
    """Project dashboard with sprint, backlog, and pipeline summary."""
    db = get_db()
    project = project_models.get_project(db, project_id)
    if project is None:
        abort(404)
    return render_template("dashboards/project.html", project=project)
