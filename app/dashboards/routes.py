from flask import Blueprint, render_template, abort

from app.db import get_db
from app.projects import models as project_models
from app.agents import models as agent_models
from app.backlog import persistence as backlog_persistence
from app.pipelines import executions as pipeline_executions
from app.pipelines import persistence as pipeline_persistence
from app.knowledge import wiki_sources
from app.sprints import persistence as sprint_persistence

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
    db = get_db()

    # Get all wiki sources (project + shared)
    wiki_list = []
    try:
        sources = wiki_sources.list_sources(db)
        for source in sources:
            project_name = ""
            if source.project_id:
                project = project_models.get_project(db, source.project_id)
                project_name = project.name if project else ""

            wiki_list.append({
                "id": source.id,
                "name": source.name,
                "source_type": source.source_type,
                "project_id": source.project_id,
                "project_name": project_name,
                "path": source.path,
            })
    except Exception:
        # Wikis not yet set up
        wiki_list = []

    return render_template(
        "dashboards/wikis.html",
        wikis=wiki_list,
    )


@bp.route("/project/<int:project_id>")
def project(project_id: int):
    """Project dashboard with sprint, backlog, and pipeline summary."""
    db = get_db()
    project = project_models.get_project(db, project_id)
    if project is None:
        abort(404)

    # Get current sprint (first active sprint)
    current_sprint = None
    sprint_data = None
    sprints = sprint_persistence.list_sprints(db, project_id)
    for sprint in sprints:
        if sprint.status == "ACTIVE":
            current_sprint = sprint
            break

    if current_sprint:
        sprint_data = {
            "id": current_sprint.id,
            "name": current_sprint.name,
            "status": current_sprint.status,
        }
        # Count tasks in sprint
        try:
            work_items = sprint_persistence.list_work_items(db, current_sprint.id)
            task_statuses = {}
            for item in work_items:
                status = getattr(item, "task_state", "pending")
                task_statuses[status] = task_statuses.get(status, 0) + 1
            sprint_data["task_statuses"] = task_statuses
            sprint_data["total_tasks"] = len(work_items)
        except Exception:
            sprint_data["task_statuses"] = {}
            sprint_data["total_tasks"] = 0

    # Get backlog summary
    backlog_summary = {}
    try:
        result = db.execute(
            "SELECT status, COUNT(*) as count FROM backlog_items WHERE project_id = ? GROUP BY status",
            (project_id,)
        ).fetchall()
        for row in result:
            backlog_summary[row[0]] = row[1]
    except Exception:
        backlog_summary = {}

    # Get active pipelines
    active_pipelines = []
    for execution in pipeline_executions.list_executions(
        db, project_id=project_id, status=ACTIVE_EXECUTION_STATUSES, limit=RECENT_LIMIT
    ):
        pipeline = pipeline_persistence.get_pipeline(db, execution.pipeline_id)
        active_pipelines.append({
            "id": execution.id,
            "name": pipeline.name if pipeline else "",
            "status": execution.status,
            "started_at": execution.started_at,
        })

    return render_template(
        "dashboards/project.html",
        project=project,
        sprint=sprint_data,
        backlog_summary=backlog_summary,
        active_pipelines=active_pipelines,
    )
