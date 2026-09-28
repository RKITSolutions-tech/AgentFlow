"""Research agent (docs/AGENT_ADAPTER.md §23, Phase A: repository-scoped).

A read-only, role-agnostic helper -- the RESEARCH-role analogue of
`app/sprints/planning_agent.PlanningAgent`, reusing exactly the same pattern:
build a role-specific prompt (skills for the RESEARCH role are pulled in via
`assemble_effective_prompt`/`skill_context`, already wired for any role by
task 52), drive an ordinary `AgentAdapter` session through its existing
`start()`/`resume()`/`status()`/`stream()` interface, then parse the agent's
final reply as JSON into a `ResearchReport`. No adapter gets a bespoke
"structured output" method or API call -- Codex/Claude/Fake are all driven
the same way every other role drives them (CLAUDE.md: adapter-specific
behaviour stays inside the adapter; nothing else depends on CLI quirks).

Locking: unlike Ralph/pipeline runs, a research session never touches
`app/projects/lock.py` -- it is read-only and advisory, so it can run
alongside other work on the same project (§23 "Locking").
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from app.agents import models
from app.agents.base import AgentAdapter, AgentContext
from app.agents.research_context import RepositoryContextLoader
from app.agents.research_report import Finding, ResearchReport, Source
from app.knowledge import ingestion as knowledge_ingestion
from app.knowledge.lookup import KnowledgeLookup
from app.prompts import assembler
from app.runs.security import redact

RESEARCH_ROLE = "RESEARCH"
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

# Limits: like Ralph's max_iterations/max_runtime_seconds (docs/RUN_AND_RALPH.md
# §22), a limit paired with a clear terminal state, sized for a single research
# pass rather than an iteration loop.
DEFAULT_TIME_LIMIT_SECONDS = 300.0
DEFAULT_COST_LIMIT_USD = 10.0
DEFAULT_MAX_SOURCES = 20

PROMPT_TEMPLATE = """You are the read-only research agent for a software project. \
Do not write, edit or commit any file; only report what you find. You have no \
web access in this session -- answer only from the context below, which may \
include relevant entries from a shared knowledge base of prior findings \
followed by repository context. A knowledge base entry is background, not \
verified fact: confirm it against the repository before relying on it, and \
say plainly what you could not verify.

Question: {question}

{repo_context}

Reply with ONE JSON object and nothing else:
{{"summary": "...", "findings": [{{"text": "...", "source_ids": ["S1"], \
"category": "..."}}], "sources": [{{"id": "S1", "file_path": "...", \
"line_range": "12-34", "excerpt": "..."}}]}}
Only give a finding a source id for a repository source you actually saw \
content from above (not a knowledge base entry -- those have no file_path/ \
line_range to cite) -- a finding with no source id is reported as \
unverified. Use at most {max_sources} sources."""


class ReportParseError(ValueError):
    """The agent's reply could not be read as a structured research report."""


@dataclass
class ResearchOutcome:
    state: str  # RUNNING | COMPLETE | FAILED | TIMED_OUT | COST_LIMIT_EXCEEDED
    report: ResearchReport | None = None
    error: str = ""


def build_prompt(question: str, repo_context: str, max_sources: int = DEFAULT_MAX_SOURCES) -> str:
    return PROMPT_TEMPLATE.format(
        question=question.strip(),
        repo_context=repo_context or "(no repository context is available for this project)",
        max_sources=max_sources,
    )


def parse_report(text: str) -> ResearchReport:
    """Extract and validate the JSON research report from an agent reply."""
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
    if not isinstance(data, dict) or not str(data.get("summary", "")).strip():
        raise ReportParseError("The research agent did not return a JSON object with a 'summary'")
    return ResearchReport.from_dict(data)


def scripted_report(report: ResearchReport) -> list[dict]:
    """Deterministic FakeAgent script: reply with `report`'s own JSON, then complete."""
    reply = "```json\n" + report.to_json() + "\n```"
    return [{"action": "message", "text": reply}, {"action": "complete"}]


class ResearchAgent:
    def __init__(
        self,
        adapter: AgentAdapter,
        db=None,
        context_loader: RepositoryContextLoader | None = None,
        clock=time.monotonic,
        poll_interval: float = 0.05,
        extra_patterns: tuple[str, ...] = (),
    ):
        self._adapter = adapter
        self._db = db
        self._context_loader = context_loader
        self._clock = clock
        self._poll_interval = poll_interval
        # Same redaction pipeline as Run commands/logs/replies
        # (app/runs/security.py): built-in secret rules plus whatever the app
        # added from AGENTFLOW_REDACT_PATTERNS, threaded in exactly like
        # PipelineManager/RalphOrchestrator do (app/pipelines/manager.py).
        self._patterns = extra_patterns

    # -- prompt assembly ------------------------------------------------------

    def _repo_context(self, repo_root: str | None, context_paths: list[str] | None) -> str:
        if repo_root is None or self._context_loader is None:
            return ""
        discovered = self._context_loader.discover(repo_root, extra_patterns=context_paths)
        return self._context_loader.render(discovered)

    def _kb_context(self, question: str) -> str:
        """Reviewed shared-knowledge-base entries relevant to `question`
        (task 48/50), rendered before repository context -- what the
        `research-use-shared-knowledge-base` skill (app.prompts.defaults)
        tells the agent to expect. Redacted like everything else this class
        puts in a prompt, even though ingestion already redacts on the way
        in (app.knowledge.ingestion): defense in depth, and a KB entry can
        also come from a human-authored /knowledge note this class never
        redacted itself."""
        if self._db is None:
            return ""
        entries = KnowledgeLookup(self._db).research(question, limit=5)
        if not entries:
            return ""
        lines = ["Relevant entries from the shared knowledge base (reviewed, but may be "
                 "stale or incomplete -- verify against the repository before relying on them):"]
        for entry in entries:
            ref = f" [[{entry.slug}]]" if entry.slug else ""
            lines.append(f"- {entry.title}{ref}: {entry.content[:400]}")
        return redact("\n".join(lines), self._patterns)[0]

    def _build_prompt(
        self,
        context: AgentContext,
        question: str,
        repo_root: str | None,
        context_paths: list[str] | None,
        max_sources: int,
    ) -> str:
        combined_context = "\n\n".join(
            part for part in (self._kb_context(question), self._repo_context(repo_root, context_paths)) if part
        )
        prompt = build_prompt(question, combined_context, max_sources)
        if self._db is not None:
            agent_type = type(self._adapter).__name__.replace("AgentAdapter", "").replace("Adapter", "").lower()
            skills_text, _ = assembler.skill_context(
                self._db, role=RESEARCH_ROLE, agent_type=agent_type, project_id=context.project_id,
            )
            if skills_text:
                prompt = f"{prompt}\n\n{skills_text}"
        return prompt

    # -- session lifecycle (mirrors PlanningAgent.start()/collect()) ----------

    def start(
        self,
        context: AgentContext,
        question: str,
        repo_root: str | None = None,
        context_paths: list[str] | None = None,
        max_sources: int = DEFAULT_MAX_SOURCES,
        options: dict | None = None,
    ) -> int:
        prompt = self._build_prompt(context, question, repo_root, context_paths, max_sources)
        opts = dict(options or {})
        opts["role"] = RESEARCH_ROLE
        session = self._adapter.start(context, prompt, opts)
        return session.id

    def collect(self, session_id: int) -> ResearchOutcome:
        session = self._adapter.status(session_id)
        if session.status in ("STARTING", "RUNNING"):
            return ResearchOutcome("RUNNING")
        if session.status != "COMPLETED":
            return ResearchOutcome("FAILED", error=f"Research session ended {session.status.lower()}")
        text = "\n".join(
            e.data for e in self._adapter.stream(session_id) if e.event_type == "AgentText"
        )
        try:
            report = parse_report(text)
        except ReportParseError as exc:
            return ResearchOutcome("FAILED", error=str(exc))
        return ResearchOutcome("COMPLETE", report=self._redact_report(report))

    def _redact_report(self, report: ResearchReport) -> ResearchReport:
        """Mask secrets in a parsed report before it is ever stored or handed
        back to a caller -- the same redaction pipeline applied to Run
        commands/logs/replies (app/runs/security.py). Unlike Runs (which keep
        an unredacted command in memory to allow a restart), a research
        report has no such need, so the report is redacted in place here,
        right where it is parsed -- there is no unredacted copy downstream to
        accidentally persist."""
        clean_summary, _ = redact(report.summary, self._patterns)
        findings = [
            Finding(
                text=redact(finding.text, self._patterns)[0],
                source_ids=list(finding.source_ids),
                category=finding.category,
            )
            for finding in report.findings
        ]
        sources = [
            Source(
                id=source.id,
                file_path=source.file_path,
                line_range=source.line_range,
                excerpt=redact(source.excerpt, self._patterns)[0],
            )
            for source in report.sources
        ]
        return ResearchReport(summary=clean_summary, findings=findings, sources=sources)

    # -- one-shot convenience --------------------------------------------------

    def research(
        self,
        context: AgentContext,
        question: str,
        repo_root: str | None = None,
        context_paths: list[str] | None = None,
        max_sources: int = DEFAULT_MAX_SOURCES,
        time_limit_seconds: float = DEFAULT_TIME_LIMIT_SECONDS,
        cost_limit_usd: float = DEFAULT_COST_LIMIT_USD,
        options: dict | None = None,
    ) -> tuple[int | None, ResearchOutcome]:
        """Start a RESEARCH session and wait for it to finish or `time_limit_seconds`
        to trip -- a single research pass, not an iteration loop, so there is
        only one limit to check between polls (Ralph's max_runtime_seconds
        shape, docs/RUN_AND_RALPH.md §22). No `app/projects/lock.py` lock is
        acquired anywhere in this path: research is read-only and advisory
        (§23 "Locking"), so it runs alongside other work on the project.

        Returns `(research_session_id, outcome)`; `research_session_id` is
        `None` when no `db` was given to the constructor (persistence is
        optional so tests can exercise the adapter path standalone).

        `cost_limit_usd` is enforced -- but only best-effort: it is checked
        against whatever `cost_usd` figure the adapter itself reports in its
        session usage metadata (docs/AGENT_ADAPTER.md §23 "Cost limits"). No
        per-model $/token rate table exists anywhere in this codebase (nor is
        one planned -- adapters shell out to CLIs, not provider APIs
        directly, so token-based estimation is out of scope until a real need
        justifies it); a session with no reported cost telemetry is bounded
        only by `time_limit_seconds`.
        """
        research_session_id = None
        if self._db is not None:
            research_session_id = models.create_research_session(
                self._db, context.project_id, question,
                time_limit_seconds=time_limit_seconds, cost_limit_usd=cost_limit_usd,
            )
        started = self._clock()
        session_id = self.start(context, question, repo_root, context_paths, max_sources, options)
        if research_session_id is not None:
            models.set_research_session_agent(self._db, research_session_id, session_id)

        outcome = self.collect(session_id)
        while outcome.state == "RUNNING":
            if self._clock() - started >= time_limit_seconds:
                self._adapter.stop(session_id)
                outcome = ResearchOutcome("TIMED_OUT", error=f"Exceeded {time_limit_seconds:g}s")
                break
            over_budget, reported_cost = self._over_cost_limit(session_id, cost_limit_usd)
            if over_budget:
                self._adapter.stop(session_id)
                outcome = ResearchOutcome(
                    "COST_LIMIT_EXCEEDED",
                    error=f"Exceeded cost limit ${cost_limit_usd:g} (adapter reported ${reported_cost:g})",
                )
                break
            time.sleep(self._poll_interval)
            outcome = self.collect(session_id)

        cost_usd = self._reported_cost(session_id)
        if outcome.state == "COMPLETE" and cost_limit_usd > 0 and cost_usd >= cost_limit_usd:
            # Every adapter today (Codex/Claude/Fake) resolves synchronously
            # within start(), so the RUNNING loop above never runs and the
            # only real cost figure arrives with the completed session --
            # still enforce the limit rather than silently storing an
            # over-budget report as an ordinary completion.
            outcome = ResearchOutcome(
                "COST_LIMIT_EXCEEDED",
                error=f"Exceeded cost limit ${cost_limit_usd:g} (adapter reported ${cost_usd:g})",
            )

        if research_session_id is not None:
            duration = self._clock() - started
            if outcome.state == "COMPLETE":
                models.complete_research_session(
                    self._db, research_session_id, outcome.report,
                    duration_seconds=duration, cost_usd=cost_usd,
                )
                self._ingest_into_knowledge_base(context.project_id, research_session_id, outcome.report)
            else:
                models.fail_research_session(
                    self._db, research_session_id, outcome.error or outcome.state,
                    duration_seconds=duration, cost_usd=cost_usd,
                )
        return research_session_id, outcome

    def _ingest_into_knowledge_base(self, project_id: int | None, research_session_id: int, report: ResearchReport) -> None:
        """Task 50.5: auto-tag a completed session's sourced findings and
        suggest them as new (unverified) wiki/knowledge-base entries. Never
        blocks or fails the session -- same "advisory-only" rule
        `RalphOrchestrator._maybe_research` applies to research itself."""
        try:
            knowledge_ingestion.ingest_research_report(self._db, project_id, research_session_id, report)
        except Exception:
            pass

    def _over_cost_limit(self, session_id: int, cost_limit_usd: float) -> tuple[bool, float]:
        reported_cost = self._reported_cost(session_id)
        return (cost_limit_usd > 0 and reported_cost >= cost_limit_usd), reported_cost

    def _reported_cost(self, session_id: int) -> float:
        session = self._adapter.status(session_id)
        usage = (session.metadata or {}).get("usage") or {}
        try:
            return float(usage.get("cost_usd", 0.0))
        except (TypeError, ValueError):
            return 0.0
