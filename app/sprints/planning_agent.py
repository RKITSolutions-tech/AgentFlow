"""Planning agent (docs/SPRINT_PLANNING_AND_BACKLOG.md §14, §31).

Runs an ordinary `AgentAdapter` session with the PLANNING role and turns its
reply into task proposals. Proposals are data only: nothing here modifies
source, commits or releases work, and the agent never approves its own output.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.agents.base import AgentAdapter, AgentContext
from app.agents.models import update_session_metadata
from app.backlog.models import BacklogItem
from app.prompts import assembler
from app.runs.models import now
from app.sprints.models import DocumentRef, Sprint

PLANNING_ROLE = "PLANNING"
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

PROMPT_TEMPLATE = """You are the planning agent for a software sprint. Do not change any files.

Sprint: {name}
Goal: {goal}
Planning profile: {profile}

Backlog items selected for this sprint:
{items}

Documentation to consult:
{docs}

Break the work into tasks. Reply with ONE JSON object and nothing else:
{{"tasks": [{{"ref": "T1", "title": "...", "description": "...",
"acceptance": ["testable criterion"], "depends_on": ["ref of another task"],
"backlog_items": [<backlog item ids this task covers>], "estimate": "S|M|L"}}]}}
Every backlog item must be covered by at least one task."""


class PlanParseError(ValueError):
    """The agent's reply could not be read as a task proposal."""


@dataclass
class TaskProposal:
    ref: str
    title: str
    description: str = ""
    acceptance: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    backlog_items: list[int] = field(default_factory=list)
    estimate: str = ""


@dataclass
class PlanOutcome:
    state: str  # RUNNING | COMPLETE | FAILED
    tasks: list[TaskProposal] = field(default_factory=list)
    error: str = ""


def build_prompt(sprint: Sprint, items: list[BacklogItem], docs: list[DocumentRef]) -> str:
    item_lines = "\n".join(
        f"- #{i.id}: {i.title or ''} {i.text}".strip() for i in items
    ) or "- (none)"
    doc_lines = "\n".join(f"- {d.reference} {d.note}".strip() for d in docs) or "- (none)"
    return PROMPT_TEMPLATE.format(
        name=sprint.name,
        goal=sprint.goal,
        profile=sprint.planning_profile,
        items=item_lines,
        docs=doc_lines,
    )


def parse_proposal(text: str, valid_item_ids: set[int]) -> list[TaskProposal]:
    """Extract and validate the JSON task list from an agent reply."""
    candidates = [m.group(1) for m in _FENCE.finditer(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    data = None
    for raw in reversed(candidates):
        try:
            data = json.loads(raw)
            break
        except ValueError:
            continue
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise PlanParseError("The planning agent did not return a JSON object with a 'tasks' list")

    tasks, refs = [], set()
    for n, raw in enumerate(data["tasks"], start=1):
        if not isinstance(raw, dict) or not str(raw.get("title", "")).strip():
            raise PlanParseError(f"Task {n} has no title")
        ref = str(raw.get("ref") or f"T{n}")
        if ref in refs:
            raise PlanParseError(f"Duplicate task ref {ref!r}")
        refs.add(ref)
        ids = []
        for value in raw.get("backlog_items") or []:
            try:
                item_id = int(value)
            except (TypeError, ValueError):
                continue
            if item_id in valid_item_ids:  # ignore ids the agent invented
                ids.append(item_id)
        tasks.append(
            TaskProposal(
                ref=ref,
                title=str(raw["title"]).strip(),
                description=str(raw.get("description", "")).strip(),
                acceptance=[str(a) for a in raw.get("acceptance") or [] if str(a).strip()],
                depends_on=[str(d) for d in raw.get("depends_on") or []],
                backlog_items=ids,
                estimate=str(raw.get("estimate", "")).strip(),
            )
        )
    if not tasks:
        raise PlanParseError("The planning agent proposed no tasks")
    return tasks


def scripted_plan(items: list[BacklogItem]) -> list[dict]:
    """Deterministic FakeAgent script: one task per backlog item (§43)."""
    tasks = [
        {
            "ref": f"T{n}",
            "title": (i.title or i.text.splitlines()[0] if (i.title or i.text) else f"Item {i.id}")[:80],
            "description": i.text,
            "acceptance": [f"Backlog item #{i.id} is implemented and verified"],
            "depends_on": [],
            "backlog_items": [i.id],
            "estimate": "M",
        }
        for n, i in enumerate(items, start=1)
    ]
    reply = "```json\n" + json.dumps({"tasks": tasks}) + "\n```"
    return [{"action": "message", "text": reply}, {"action": "complete"}]


class PlanningAgent:
    def __init__(self, adapter: AgentAdapter, db=None, fake_script_from_items: bool = False):
        self._adapter = adapter
        self._db = db
        self._scripted = fake_script_from_items

    def start(
        self,
        context: AgentContext,
        sprint: Sprint,
        items: list[BacklogItem],
        docs: list[DocumentRef],
    ) -> int:
        options: dict = {"role": PLANNING_ROLE}
        if self._scripted:
            options["script"] = scripted_plan(items)
        prompt = build_prompt(sprint, items, docs)
        skill_names: list[str] = []
        agent_type = ""
        if self._db is not None:
            agent_type = type(self._adapter).__name__.replace("AgentAdapter", "").replace("Adapter", "").lower()
            skills_text, skill_names = assembler.skill_context(
                self._db, role=PLANNING_ROLE, agent_type=agent_type, project_id=context.project_id,
            )
            if skills_text:
                prompt = f"{prompt}\n\n{skills_text}"
        session = self._adapter.start(context, prompt, options)
        if self._db is not None:
            # Compliance/audit trail (docs/AGENT_ADAPTER.md §23.2, task 53): the
            # planning session never touches execution_prompts, so the injected
            # skill manifest is recorded on the agent session itself.
            update_session_metadata(
                self._db, session.id,
                injected_context={
                    "role": PLANNING_ROLE, "agent_type": agent_type, "skills": skill_names,
                    "assembled_at": now(),
                },
            )
        return session.id

    def collect(self, session_id: int, valid_item_ids: set[int]) -> PlanOutcome:
        session = self._adapter.status(session_id)
        if session.status in ("STARTING", "RUNNING"):
            return PlanOutcome("RUNNING")
        if session.status != "COMPLETED":
            return PlanOutcome("FAILED", error=f"Planning session ended {session.status.lower()}")
        text = "\n".join(
            e.data for e in self._adapter.stream(session_id) if e.event_type == "AgentText"
        )
        try:
            return PlanOutcome("COMPLETE", tasks=parse_proposal(text, valid_item_ids))
        except PlanParseError as exc:
            return PlanOutcome("FAILED", error=str(exc))


def _planning_kind(app_config) -> str:
    return str(app_config.get("PLANNING_AGENT", "codex")).lower()


def _local_planning_model(app_config, db):
    """Resolves the Settings 'local' catalog entry PLANNING_AGENT=local names
    via PLANNING_MODEL, raising `ModelCatalogConfigError` if it's missing,
    disabled, or was never configured."""
    from app.settings.models import resolve_local_model

    return resolve_local_model(
        db, app_config.get("PLANNING_MODEL", ""), label="AGENTFLOW_PLANNING_MODEL"
    )


def _local_planning_adapter(app_config, db):
    """Builds the real adapter backing PLANNING_AGENT=local: which CLI wire
    protocol the catalog entry speaks is named separately by
    PLANNING_LOCAL_ADAPTER, since the catalog row itself only carries a
    base_url/api_key (app/settings/models.py)."""
    _local_planning_model(app_config, db)  # validate early; raises if unusable
    if str(app_config.get("PLANNING_LOCAL_ADAPTER", "codex")).lower() == "claude":
        from app.agents.claude import ClaudeAdapter

        return ClaudeAdapter(db=db, execution_provider=_host_provider(app_config))
    from app.agents.codex import CodexAdapter

    return CodexAdapter(db=db, execution_provider=_host_provider(app_config))


def build_planning_agent(app_config, db) -> PlanningAgent:
    """The configured planning agent ("fake" for deterministic CI runs)."""
    kind = _planning_kind(app_config)
    if kind == "fake":
        from app.agents.fake import FakeAgentAdapter

        return PlanningAgent(FakeAgentAdapter(db), db, fake_script_from_items=True)
    if kind == "claude":
        from app.agents.claude import ClaudeAdapter

        return PlanningAgent(
            ClaudeAdapter(db=db, execution_provider=_host_provider(app_config)), db
        )
    if kind == "local":
        return PlanningAgent(_local_planning_adapter(app_config, db), db)
    from app.agents.codex import CodexAdapter

    return PlanningAgent(CodexAdapter(db=db, execution_provider=_host_provider(app_config)), db)


def _host_provider(app_config):
    from app.execution.host import HostExecutionProvider

    return HostExecutionProvider(app_config["DATABASE_PATH"], app_config["ALLOWED_PROJECT_ROOTS"])


def build_context(app_config, project_id: int, repository_path: str, db=None) -> AgentContext:
    """Where the agent runs. Only real adapters need an execution context
    (created once per planning run, not per status poll). For
    PLANNING_AGENT=local, `db` resolves the configured catalog entry and its
    model_id is set on the context so the adapter's own local-model handling
    (CodexAdapter's `-c model_providers.local...`/ClaudeAdapter's
    ANTHROPIC_BASE_URL, both keyed off `context.model`) applies exactly as it
    would for a session started interactively against that same model."""
    kind = _planning_kind(app_config)
    if kind == "fake":
        return AgentContext(project_id=project_id, working_directory=repository_path)
    exec_context = _host_provider(app_config).create_context(
        {"working_directory": repository_path, "environment": {}, "target": ""}
    )
    model = _local_planning_model(app_config, db).model_id if kind == "local" else None
    return AgentContext(
        project_id=project_id,
        working_directory=repository_path,
        execution_provider="host",
        execution_target=str(exec_context.id),
        model=model,
    )
