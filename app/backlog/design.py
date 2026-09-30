"""Backlog "Design this item" action (docs/SPRINT_PLANNING_AND_BACKLOG.md §51).

An ad-hoc, blocking trigger from a Backlog item, the same shape as
`app/backlog/research.py`'s Research action (same module reused for
adapter/repository resolution: `app.pipelines.manager.default_agent_factory`,
`app.sprints.planning_agent.build_context`) -- but its reply seeds draft
`backlog_test_proposals` rows, not `research_sessions`/an Artifact. Those two
tables assume a `ResearchReport` (summary/findings/sources); a test proposal
is a different shape (title/description/pytest_node_id), so this module
drives the adapter directly rather than going through `ResearchAgent`, the
same way `app.sprints.planning_agent.PlanningAgent` drives its own session
for its own (task-proposal) reply shape rather than reusing `ResearchAgent`
either.

Proposals are advisory-only, same rule as Research (§50) and Planning: a
person accepts or dismisses each one before it becomes anything real. An
ACCEPTED proposal is promoted into a real `acceptance_criteria` row later, at
sprint-approval time (`app.acceptance.service.sync_from_work_items`) -- a
fresh Backlog item has no `planned_work_items` row for one to attach to yet.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

from app.agents.base import AgentContext
from app.agents.research_context import RepositoryContextLoader
from app.backlog import persistence
from app.backlog.models import BacklogItem
from app.pipelines.manager import default_agent_factory
from app.runs.security import redact
from app.sprints.planning_agent import build_context

logger = logging.getLogger(__name__)

DESIGN_ROLE = "DESIGN"
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

# Same order-of-magnitude bound as the Research action (§50): a single
# proposal pass, not an iteration loop.
DEFAULT_TIME_LIMIT_SECONDS = 300.0
DEFAULT_COST_LIMIT_USD = 5.0

PROMPT_TEMPLATE = """You are the test-design agent for a software project. Do not write, edit or \
commit any file; only propose tests that would prove a Backlog item works. You have no web access \
in this session -- answer only from the repository context below.

Backlog item:
{question}

{repo_context}

Reply with ONE JSON object and nothing else:
{{"proposals": [{{"title": "...", "description": "...", \
"pytest_node_id": "path/to/test_file.py::test_name"}}]}}
Only give a proposal a `pytest_node_id` for a test you can see already exists in the repository \
context above; leave it "" if the test does not exist yet -- a person fills that in later once a \
real test is written for it. Propose at most 8 tests."""


class DesignParseError(ValueError):
    """The agent's reply could not be read as a list of test proposals."""


@dataclass
class TestProposal:
    title: str
    description: str = ""
    pytest_node_id: str = ""


@dataclass
class DesignOutcome:
    state: str  # RUNNING | COMPLETE | FAILED | TIMED_OUT
    proposals: list[TestProposal] = field(default_factory=list)
    error: str = ""


@dataclass
class DesignRunResult:
    design_session_id: int | None
    proposal_ids: list[int]
    outcome: DesignOutcome


def build_question(item: BacklogItem) -> str:
    title = item.title.strip()
    text = item.text.strip()
    if title and text:
        return f"{title}\n\n{text}"
    return title or text or f"Backlog item #{item.id}"


def build_prompt(question: str, repo_context: str) -> str:
    return PROMPT_TEMPLATE.format(
        question=question.strip(),
        repo_context=repo_context or "(no repository context is available for this project)",
    )


def parse_design_reply(text: str) -> list[TestProposal]:
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
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        raise DesignParseError("The design agent did not return a JSON object with a 'proposals' list")
    proposals = []
    for n, raw in enumerate(data["proposals"], start=1):
        if not isinstance(raw, dict) or not str(raw.get("title", "")).strip():
            raise DesignParseError(f"Proposal {n} has no title")
        proposals.append(TestProposal(
            title=str(raw["title"]).strip(),
            description=str(raw.get("description", "")).strip(),
            pytest_node_id=str(raw.get("pytest_node_id", "")).strip(),
        ))
    return proposals


def scripted_proposals(proposals: list[TestProposal]) -> list[dict]:
    """Deterministic FakeAgent script (PLANNING_AGENT=fake), mirroring
    `research_agent.scripted_report`."""
    reply = "```json\n" + json.dumps({
        "proposals": [
            {"title": p.title, "description": p.description, "pytest_node_id": p.pytest_node_id}
            for p in proposals
        ]
    }) + "\n```"
    return [{"action": "message", "text": reply}, {"action": "complete"}]


def _fake_proposals(item: BacklogItem) -> list[TestProposal]:
    subject = item.title or item.text or f"item #{item.id}"
    return [TestProposal(title=f"{subject} behaves as described"[:120], description=item.text[:500])]


def _kind(app_config) -> str:
    return str(app_config.get("PLANNING_AGENT", "codex")).lower()


def _provider(app_config):
    from app.execution.host import HostExecutionProvider

    return HostExecutionProvider(app_config["DATABASE_PATH"], app_config["ALLOWED_PROJECT_ROOTS"])


def build_agent_context(app_config, project, db) -> tuple[AgentContext, str | None]:
    """Same repository resolution as Research (§50): primary repo, else the
    project's first repository, else the first allowed root."""
    repo = next((r for r in project.repositories if r.is_primary), None) or (
        project.repositories[0] if project.repositories else None
    )
    path = repo.path if repo else app_config["ALLOWED_PROJECT_ROOTS"][0]
    return build_context(app_config, project.id, path, db), (repo.path if repo else None)


def run_item_design(app_config, db, project, item: BacklogItem) -> DesignRunResult:
    """Ask a DESIGN-role agent to propose tests for `item`, wait for it to
    finish (or `DEFAULT_TIME_LIMIT_SECONDS` to trip), and store each proposal
    as a `backlog_test_proposals` row. `item.status` is never touched, same
    as Research (§50)."""
    kind = _kind(app_config)
    provider = _provider(app_config)
    adapter = default_agent_factory(app_config, provider)(db)
    patterns = tuple(app_config.get("REDACT_PATTERNS", ()))

    context, repo_root = build_agent_context(app_config, project, db)
    question = build_question(item)
    repo_context = ""
    if repo_root:
        loader = RepositoryContextLoader()
        repo_context = loader.render(loader.discover(repo_root))
    prompt = build_prompt(question, repo_context)

    proposals_for_script = _fake_proposals(item)
    options = {"role": DESIGN_ROLE}
    if kind == "fake":
        options["script"] = scripted_proposals(proposals_for_script)

    session = adapter.start(context, prompt, options)
    design_session_id = session.id

    started = time.monotonic()
    outcome = _collect(adapter, design_session_id, patterns)
    while outcome.state == "RUNNING":
        if time.monotonic() - started >= DEFAULT_TIME_LIMIT_SECONDS:
            adapter.stop(design_session_id)
            outcome = DesignOutcome("TIMED_OUT", error=f"Exceeded {DEFAULT_TIME_LIMIT_SECONDS:g}s")
            break
        time.sleep(0.05)
        outcome = _collect(adapter, design_session_id, patterns)

    proposal_ids = []
    if outcome.state == "COMPLETE":
        for p in outcome.proposals:
            proposal_ids.append(persistence.add_test_proposal(
                db, item.id, design_session_id, p.title, p.description, p.pytest_node_id,
            ))
    return DesignRunResult(design_session_id=design_session_id, proposal_ids=proposal_ids, outcome=outcome)


def _collect(adapter, session_id: int, patterns: tuple[str, ...]) -> DesignOutcome:
    session = adapter.status(session_id)
    if session.status in ("STARTING", "RUNNING"):
        return DesignOutcome("RUNNING")
    if session.status != "COMPLETED":
        return DesignOutcome("FAILED", error=f"Design session ended {session.status.lower()}")
    text = "\n".join(e.data for e in adapter.stream(session_id) if e.event_type == "AgentText")
    try:
        proposals = parse_design_reply(text)
    except DesignParseError as exc:
        return DesignOutcome("FAILED", error=str(exc))
    clean = [
        TestProposal(
            title=redact(p.title, patterns)[0],
            description=redact(p.description, patterns)[0],
            pytest_node_id=p.pytest_node_id,
        )
        for p in proposals
    ]
    return DesignOutcome("COMPLETE", proposals=clean)


def item_design_history(db, item_id: int) -> list:
    return persistence.list_test_proposals(db, item_id)
