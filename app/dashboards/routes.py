from flask import Blueprint, render_template, abort

from app.db import get_db
from app.projects import models as project_models
from app.agents import models as agent_models
from app.backlog import persistence as backlog_persistence
from app.pipelines import executions as pipeline_executions
from app.pipelines import persistence as pipeline_persistence

bp = Blueprint(
    "dashboards",
    __name__,
    url_prefix="/dashboards",
)

RECENT_LIMIT = 5
ACTIVE_EXECUTION_STATUSES = ["RUNNING", "PAUSED"]


@bp.route("/home")
def home():
    """Home dashboard with recent sessions, pipelines, and backlog items."""
    db = get_db()
    project_names = {p.id: p.name for p in project_models.list_projects(db, archived=None)}

    sessions = [
        {
            "id": s.id,
            "title": agent_models.session_title(db, s),
            "project_name": project_names.get(s.project_id, ""),
            "status": s.status,
            "last_activity_at": s.last_activity_at,
        }
        for s in agent_models.list_sessions(db, role="GENERAL", limit=RECENT_LIMIT)
    ]

    pipelines = []
    for execution in pipeline_executions.list_executions(
        db, status=ACTIVE_EXECUTION_STATUSES, limit=RECENT_LIMIT
    ):
        pipeline = pipeline_persistence.get_pipeline(db, execution.pipeline_id)
        pipelines.append(
            {
                "id": execution.id,
                "project_id": execution.project_id,
                "name": pipeline.name if pipeline else "",
                "project_name": project_names.get(execution.project_id, ""),
                "status": execution.status,
                "started_at": execution.started_at,
            }
        )

    backlog_items = [
        {
            "id": item.id,
            "project_id": item.project_id,
            "title": item.title,
            "project_name": project_names.get(item.project_id, ""),
            "status": item.status,
            "created_at": item.created_at,
        }
        for item in backlog_persistence.list_items(
            db, limit=RECENT_LIMIT, order_by="created_at"
        )
    ]

    return render_template(
        "dashboards/home.html",
        sessions=sessions,
        pipelines=pipelines,
        backlog_items=backlog_items,
    )


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
