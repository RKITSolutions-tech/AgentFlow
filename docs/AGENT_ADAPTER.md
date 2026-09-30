# AgentFlow Agent Adapter Design

## 1. Purpose

This document defines the common abstraction used by AgentFlow to interact with coding agents such as Codex, Claude Code, Gemini CLI and OpenCode.

The rest of AgentFlow should not depend on agent-specific CLI behaviour.

## 2. Initial Agents

```text
CodexAdapter
ClaudeAdapter
GeminiAdapter
OpenCodeAdapter
FakeAgentAdapter
```

Codex was the first real implementation priority (§17); Claude Code followed
as a second real adapter (§18). Gemini and OpenCode remain unimplemented.

## 3. Core Interface

```python
class AgentAdapter:
    def available(self) -> bool: ...
    def version(self) -> str: ...
    def capabilities(self): ...
    def discover_sessions(self, project): ...
    def start(self, context, prompt, options): ...
    def resume(self, session_id, prompt, options): ...
    def send(self, session_id, content): ...
    def stop(self, session_id): ...
    def status(self, session_id): ...
    def stream(self, session_id): ...
```

This is the canonical AgentAdapter interface.

## 4. Adapter Responsibilities

Adapters translate AgentFlow concepts into agent-specific commands and session semantics.

Responsibilities:

```text
binary discovery
version detection
session discovery
session start/resume
prompt submission
output streaming
process control
external session ID mapping
capability mapping
error translation
```

Adapters should not own Sprint planning, Ralph policy, pipeline execution, Git strategy, test execution, acceptance evaluation or artifact storage.

## 5. Agent Session

Suggested fields:

```text
id
project_id
agent_type
role
external_session_id
execution_provider
execution_target
status
started_at
last_activity_at
metadata
```

Roles:

```text
PLANNING
IMPLEMENTATION
VERIFICATION
GENERAL
```

## 6. Session Discovery

Where an agent supports existing session discovery, AgentFlow should surface those sessions. This is a high-priority Phase 1 capability because it contributes directly to CloudCLI-like usability.

## 7. Session Persistence

AgentFlow stores its own session metadata even if the underlying agent has native persistence.

```text
AgentFlow session ID
↔
agent-native session ID
```

## 8. Prompt Construction

Adapters receive the final effective prompt from AgentFlow. Prompt composition belongs to AgentFlow's context/prompt services.

The adapter should not silently alter task semantics.

Reusable prompt fragments may include:

```text
planning instructions
implementation instructions
Ralph instructions
test-failure analysis
visual review
acceptance verification
```

## 9. Prompt and Reply Capture

All sent prompts and received replies must be emitted as events and persisted.

This includes:

```text
full prompt
prompt fragments
agent response
tool messages where available
CLI metadata where available
```

## 10. Streaming

Adapter output should be converted into a common event stream.

Possible event types:

```text
AgentText
AgentToolCall
AgentToolResult
AgentStatus
AgentError
AgentComplete
```

If an agent does not expose structured output, the adapter may fall back to text stream parsing.

## 11. Capabilities

Adapters should declare capabilities such as:

```text
session_discovery
resume
structured_events
tool_events
image_input
persistent_session
approval_requests
model_selection
MCP
```

UI and orchestration can adapt to those capabilities.

## 12. Approvals

Agent approval requests should be translated into AgentFlow events. Future UI may support approve, deny and approve-for-session actions.

Agent CLI approval requests are adapter-level events because they may arise inside a running AgentStep. They are distinct from declared pipeline ManualSteps, but both use the same waiting-for-human UI treatment.

Some agent CLIs also raise structured clarifying questions mid-session —
multiple choice, optionally with a free-text "other" answer — rather than a
bare approve/deny. CloudCLI renders these as a selectable option list plus
an editable "other" field; AgentFlow's replacement should match that. These
are adapter-level events distinct from approvals, using the same
waiting-for-human UI treatment, and the structured option list should be
preserved end-to-end rather than flattened into a plain text prompt.

Observed reference behaviour (CloudCLI v1.37.3, answered Claude Code
`AskUserQuestion` in an AgentFlow session): the question renders as its own
inline conversation item after the agent's prose, not as an approval-style
banner. Structured payload and rendering:

```text
questions[].question     question text
questions[].header       short chip label shown above the question
questions[].multiSelect  single vs multiple choice
questions[].options[]    { label, description } - one radio row each;
                         "(Recommended)" is just part of the label text
answer                   { "<question>": "<selected label>" }
```

The answered state shows the chosen option highlighted and the others greyed
out.

The live (pending) state, observed in the same CloudCLI version, is different
and is the model to match:

```text
- the message input is replaced by a docked panel headed
  "CLAUDE NEEDS YOUR INPUT" plus the header chip
- options are numbered rows (1, 2, 3 ...), each with label + description,
  selectable by click or by pressing the number key
- the last row is "Other..." (key 0); selecting it reveals a
  "Type your answer..." text field inside the panel
- "Skip" (Esc) and "Submit" (Enter) actions; Submit stays disabled until an
  option is chosen or the Other text is non-empty
- the inline conversation item shows a "Running" badge while pending
- Skip marks the item "Skipped" and returns a tool-rejected error to the
  agent ("the user doesn't want to proceed ... wait for the user"), so a
  skip is a distinct outcome from an answer and the agent stops and waits
```

"Other" is part of the tool's UI contract, not an option supplied by the
agent, so AgentFlow must add it itself for every clarifying question. The
structured form is a native Claude Code tool; Codex has no known equivalent.

Decided (task 10.1): a clarifying question is a distinct `ClarifyingQuestion`
AgentEvent whose data is `{"question_id": N}`, pointing at a row in
`agent_questions` (`app/agents/models.py`). The row holds header, question,
multi-select flag, agent-supplied options, and a `PENDING`/`ANSWERED`/`SKIPPED`
status; "Other" is added by `display_options()` and never stored. Skip is a
distinct final outcome from an answer. All state is in SQLite, so `resume()`
finds pending questions via `list_clarifying_questions(status="PENDING")`.

Detection for agents without a native structured tool (task 10.3): Codex
(`codex exec --json`) only emits text, so detection is explicit rather than
heuristic. An agent that wants to ask emits a fenced `agentflow-question`
block containing a JSON object (or list) with `question`, `options`, and
optionally `header` and `multi_select`. `app/agents/questions.py` extracts
valid blocks from the reply, and `CodexAdapter._sync_events` records them as
`ClarifyingQuestion` events after the remaining prose (the `AgentText` event
is omitted if nothing else was said). Malformed blocks and fuzzy prose such as
"please choose one" are left as ordinary text, so a normal message is never
altered or misread. The teaching text is the reusable prompt fragment
`QUESTION_PROTOCOL_INSTRUCTIONS`; per section 8, prompt composition decides
when to include it, and nothing includes it yet. Because `codex exec` ends its
turn after asking, the answer goes back as a plain-text message that starts
the next turn.

Native handling for Claude Code (implemented in `app/agents/claude.py`, §18):
unlike Codex, `claude -p --output-format stream-json` emits the question as a
`tool_use` content block named `AskUserQuestion` on an `assistant` event, with
`questions[].question`/`header`/`options`/`multiSelect` already structured —
no text-fence detection needed. `ClaudeAdapter._extract_claude_questions`
reads that block directly and passes the tool_use block's own `id` as
`external_id` to `create_clarifying_question`. As with Codex, the turn ends
after asking, so the answer is delivered as a plain-text message on the next
`send()`/`resume()`; `ClaudeAdapter` does not reconstruct a `tool_result`
block for it.

Open questions:

```text
should "other" ever carry AI-suggested prefill text, or always start
  blank
where should QUESTION_PROTOCOL_INSTRUCTIONS be injected once a prompt
  composition service exists
```

## 13. Execution Provider

Agents run through HostExecutionProvider or DockerExecutionProvider. Adapters should request execution through the provider abstraction rather than invoking subprocesses directly.

## 14. Working Directory and Multi-Repository Context

Every session has an explicit Project/repository working context. Multi-repository Projects may use a Project root or selected primary repository.

AgentFlow provides repository paths and permissions in context. Adapters do not implement multi-repository semantics themselves.

## 15. Errors

Agent-specific errors map to common categories:

```text
AGENT_NOT_INSTALLED
AUTHENTICATION_REQUIRED
SESSION_NOT_FOUND
PROCESS_FAILED
OUTPUT_PARSE_ERROR
TIMEOUT
USER_CANCELLED
UNKNOWN_AGENT_ERROR
```

## 16. Fake Agent

FakeAgentAdapter is required early for deterministic tests.

It should support scripted behaviour such as:

```text
emit known messages
wait
create fixture file
simulate failure
simulate retry
complete successfully
```

## 17. Codex Adapter

Codex is the Phase 1 real-agent target.

Initial capability target:

```text
binary discovery
version
start session
resume where supported
stream output
stop
session persistence
Project working directory
prompt/reply capture
session discovery where supported
```

Requirements and degraded-mode behaviour (implemented in `app/agents/codex.py`,
task 4.5):

- The `codex` binary must be resolvable (`shutil.which`) and already
  authenticated (`codex login`); AgentFlow does not manage Codex
  authentication itself.
- `capabilities()` is a static declaration of what `CodexAdapter` as a class
  supports (`resume`, `session_discovery`, `structured_events`) — it
  describes the adapter type, not whether the binary is currently
  reachable. `available()`/`version()` (section "binary discovery") answer
  the "is it usable right now" question separately, via cached
  `shutil.which`/`codex --version` detection.
- If the binary can't actually be launched when `start`/`resume`/`send` try
  to run it (missing, permissions, etc.), the adapter does not let the raw
  `OSError` escape: it records an `AgentError` event and marks the session
  `FAILED`, the same way any other turn failure is reported.
- Session discovery reads `$CODEX_HOME/sessions/**/rollout-*.jsonl`
  (`CODEX_HOME` defaults to `~/.codex`) to import native Codex sessions
  whose `cwd` matches the Project's primary repository path; it is a
  best-effort filesystem scan, not a `codex` subprocess call, so it degrades
  gracefully (returns only already-tracked sessions) when that directory
  doesn't exist.
- A model chosen from the Settings model catalog's `local` provider (a
  self-hosted, OpenAI-compatible server such as LM Studio) is not just
  passed via `-m`: `_model_flags` also appends `-c model_provider=...`/
  `model_providers.local.base_url`/`wire_api` overrides, and, only if that
  catalog entry has an API key, `-c model_providers.local.env_key=...` plus
  an `AGENTFLOW_LOCAL_MODEL_API_KEY` environment variable (never a command
  line argument, so it needs no redaction).
- An `item.completed` item whose type isn't `agent_message`/`error`
  (`command_execution`, `file_change`, `mcp_tool_call`, `web_search`,
  `todo_list`) becomes an `AgentToolCall` event via `_summarize_codex_item`,
  the same event type and compact-label convention `ClaudeAdapter` uses
  (§18), for the chat UI's live activity indicator. `reasoning` items are
  deliberately not surfaced (too noisy). The exact field names read off
  each item type were not confirmed against a live `codex exec --json`
  stream when this was written (only `agent_message`/`error` were); each
  branch falls back to a humanized item-type label
  (`_CODEX_ITEM_FALLBACK_LABELS`) if its expected detail field is absent, so
  a wrong guess degrades to a generic label rather than silence or garbage —
  worth re-checking against a real stream if the labels look off.

## 18. Claude Adapter

Claude Code is the second real adapter, implemented in `app/agents/claude.py`
following the same structure as `CodexAdapter` (§17).

Initial capability target (matches Codex's, minus `image_input`, for which
the Claude CLI's print mode has no equivalent flag):

```text
binary discovery
version
start session
resume where supported
stream output
stop
session persistence
Project working directory
prompt/reply capture
session discovery where supported
```

Requirements and behaviour:

- The `claude` binary must be resolvable (`shutil.which`) and already
  authenticated; AgentFlow does not manage Claude Code authentication
  itself.
- Turns run as `claude -p --output-format stream-json --verbose`
  (`--resume <external_session_id>` for a resumed turn), and `--json`-style
  stdout lines are translated by `_sync_events`: `system`/`init` supplies the
  external session id (Codex's analogue is `thread.started`), `assistant`
  message content blocks become `AgentText` events (or an `AskUserQuestion`
  `tool_use` block, per §12), and the terminal `result` event
  (`is_error` true/false) maps to `AgentComplete`/`AgentError`, matching
  Codex's `turn.completed`/`turn.failed`.
- Any other `tool_use` block (`Read`, `Edit`, `Bash`, etc.) becomes an
  `AgentToolCall` event whose data is a compact `Name(detail)` label built by
  `_summarize_tool_call` (e.g. `Read(app.py)`, `Bash(pytest -q)`) — the same
  shape Codex's `AgentToolCall` events use (§17) — so the chat UI's live
  activity indicator (`app/templates/sessions/chat.html`) has something to
  show between a prompt being sent and the next `AgentText`/`AgentComplete`.
  `tool_result` content in the CLI's `user`-role echo lines is not parsed;
  only the call itself is surfaced.
- `--permission-mode` accepts `default`, `plan` and `acceptEdits`;
  `bypassPermissions` is deliberately not offered from the UI, mirroring
  Codex withholding `danger-full-access`.
- If the binary can't actually be launched, the adapter records an
  `AgentError` event and marks the session `FAILED` rather than letting the
  raw `OSError` escape — same degraded-mode contract as Codex.
- Session discovery reads `$CLAUDE_CONFIG_DIR/projects/*/*.jsonl`
  (`CLAUDE_CONFIG_DIR` defaults to `~/.claude`) for native transcripts whose
  first line's `cwd` matches the Project's primary repository path; a
  best-effort filesystem scan, degrading gracefully when the directory is
  absent, same as Codex's rollout-file scan.
- A `local` catalog model (§17) is applied via `ANTHROPIC_BASE_URL` and, if
  the entry has an API key, `ANTHROPIC_AUTH_TOKEN` environment variables —
  the Claude CLI's own documented mechanism for pointing at a custom,
  Anthropic-API-compatible server, so no `-c`-style config flag is needed.

Gemini and OpenCode remain unimplemented; they should follow the same
pattern once needed.

The sprint planning agent (`app/sprints/planning_agent.py`,
docs/SPRINT_PLANNING_AND_BACKLOG.md §31/§49) picks between `CodexAdapter` and
`ClaudeAdapter` the same way: `AGENTFLOW_PLANNING_AGENT=claude` runs a
`ClaudeAdapter` session; `AGENTFLOW_PLANNING_AGENT=local` resolves
`AGENTFLOW_PLANNING_MODEL` against the Settings `local` catalog (raising
`ModelCatalogConfigError`, `app/settings/models.py`, if it's missing/disabled)
and hands the resolved `model_id` to whichever of the two adapters
`AGENTFLOW_PLANNING_LOCAL_ADAPTER` names — a `local` catalog row has no field
for which CLI's wire protocol it speaks (§17 vs this section), so that has to
be named separately.

The generic pipeline engine's AGENT step handler (`PipelineEngine._h_agent`,
`app/pipelines/engine.py`, task 56) reuses the same `resolve_local_model`
lookup at the step level: an element's `config["model"]` names a `local`
catalog `model_id`, resolved and set on the `AgentContext` the step builds
(`ModelCatalogConfigError` fails the step if it's missing/disabled, same as
planning). `PipelineManager.default_agent_factory` (`app/pipelines/manager.py`)
picks up the matching global knobs (`PLANNING_AGENT=local`/`PLANNING_MODEL`/
`PLANNING_LOCAL_ADAPTER`, shared with the planning agent) so the pipeline as a
whole runs through a local-capable adapter class; the per-step `config["model"]`
override is independent of that and works with whichever adapter class the
engine is already using.

## 19. UI Integration

The Session screen is agent-agnostic and should support:

```text
conversation
status
agent identity
prompt input
streaming output
files
Git
terminal
session history
```

Agent-specific controls may appear conditionally based on capability discovery.

## 20. Phase 1 Scope

Phase 1 strongly focuses on CloudCLI replacement:

```text
FakeAgent
CodexAdapter
agent discovery
session creation
session list
session resume where possible
streaming
prompt entry
prompt/reply persistence
stop
CloudCLI-style Session UI
```

## 21. Phase 2 Scope

Phase 2 adds:

```text
additional agents
pipeline-controlled agent roles
prompt library selection
Ralph instruction blocks
approval integration
richer structured tool-event display
visual execution integration
```

## 22. Context Files and Preloading

Agents should not have to rediscover a Project's structure and conventions
from scratch each session (e.g. reading adapter source files to infer the
`AgentAdapter` contract, as happened before this section existed). AgentFlow
should preload sessions with explicit context files, the same way Claude
Code itself auto-loads `CLAUDE.md`.

This is part of the context/prompt service described in section 8 — adapters
remain unaware of *how* context was assembled; they only ever receive the
final effective prompt.

Two tiers:

```text
project-level context file(s)
  - analogous to CLAUDE.md
  - configured per Project (repository-checked-in or Project metadata)
  - loaded automatically into every session's effective prompt for
    that Project

on-demand context files
  - additional docs, style guides, or subdirectory-scoped context files
  - not preloaded into every prompt (avoids bloating every session with
    the full universe of docs)
  - resolvable/fetchable within a session when relevant
```

The context-file system itself should be agent-agnostic: it applies the
same way whether the underlying adapter is Codex, Claude, Gemini, or
OpenCode, even though some agent CLIs also have their own native
instruction-file conventions (e.g. Codex's own config/instructions files),
which adapters may additionally surface but should not be relied on as the
only mechanism.

Open questions to resolve before implementation:

(Proposed answers: `PHASE2_PLANNING.md` §2.)

```text
where project-level context files live (repository file vs Project metadata)
how on-demand files are surfaced (pre-injected on request vs an
  agent-callable lookup/tool)
whether context-file assembly is purely an AgentFlow-side prompt
  composition step, or partly delegated to adapters with native support
```

## 23. Research Agent Role

Phase A (repository-scoped, task 45) is implemented. `RESEARCH` is one of the session roles (§5,
`app/agents/base.AGENT_ROLES`), but it is **not** a new `AgentAdapter` method: like the `PLANNING` role
(`app/sprints/planning_agent.PlanningAgent`), it is a role-agnostic helper --
`app/agents/research_agent.ResearchAgent` -- that drives any adapter through its existing
`start()`/`resume()`/`status()`/`stream()`, so `FakeAgentAdapter` supports it exactly the same way Codex and
Claude do, with no adapter-specific code. Phase B (shared knowledge base, §23.1, task 48) and Phase C (web,
task 49) are still proposed, not built; Phase A does not depend on either.

- **Read-only.** No file writes, no commits, no project mutation. `ResearchAgent` only ever calls the adapter
  with the prompt built for it; the prompt itself instructs the agent not to write, edit or commit anything.
- **Lookup order: project repository, then the shared knowledge base, then the web.** Each layer is tried
  before the next, and a report says which layer each finding came from. Many patterns repeat across projects, so
  the web is the last resort, not the default.
  - *Phase A, repository (implemented):* `app/agents/research_context.RepositoryContextLoader` discovers a
    bounded set of files under the project's repository root -- `README*`, `docs/**` (design docs like
    `docs/HIGH_LEVEL_DESIGN.md`/`docs/AGENT_ADAPTER.md` are fed in with full content), dependency manifests, and
    a path+size listing (not inlined) of `src/`/`app/`/`tests/`. Path safety reuses
    `app/security.validate_repository_path` for the root and the same glob/symlink-escape technique as
    `app/prompts/assembler.resolve_context_files` per file (absolute patterns and symlinks that resolve outside
    the repository are rejected), with its own caps: under 500 files and under 50MB total.
  - *Phase B, shared knowledge base (proposed, task 48):* one central store used by every project on the
    installation -- see §23.1.
  - *Phase C, web (proposed, task 49):* the agent's own tools (the Claude CLI has them and works on the OS login
    without an API key) do the searching; AgentFlow mediates fetches so results land in the knowledge base.
- **Shared knowledge base (central, multi-project).** Located by `AGENTFLOW_KNOWLEDGE_DIR` (default `knowledge/`
  beside the database; may be a git repository, which gives history and sharing for free). Plain files on disk
  (path-checked, size-capped) with metadata and search in SQLite. Two entry kinds:
  - `web_cache`: a fetched page (API docs, language and library references such as Python). Source URL, content
    hash, fetched-at, expiry by kind (versioned docs live longer than "latest" pages). Stale entries are refreshed
    on demand and marked, never silently served past expiry.
  - `note`: a distilled, reusable finding or pattern (how to structure X, gotchas of library Y), written by the
    research agent or a person. Has a title, body, sources, tags (language, library, version, topic) and a
    `confidence` (`unverified` | `reviewed`).
  Every entry records provenance (which project, session and step created it, and when) and a use count. A report
  cites entries by id, so a finding stays traceable to what was read when.
- **The agent updates it, not just reads it.** After a session the agent proposes new or improved `note` entries and
  refreshed `web_cache` entries. Web pages are stored automatically (redacted first). Notes derived from a
  project's repository are *proposals*: they enter as `unverified`, hidden from other projects until a person
  promotes them, so one project's confidential detail cannot leak into another. Notes derived only from public web
  sources may be shared straight away as `unverified`.
- **Sharing rules.** Nothing from a repository or a project secret is written to the shared store verbatim or
  sent to the web: only a distilled note, after redaction and (for repository-derived notes) human promotion.
  Entries carry a `scope` (`global` or a project id); the agent only reads `global` entries plus those of the
  current project. A person can view, search, edit, pin, refresh, merge duplicates and delete entries.
- **Structured report (implemented).** The reply is a report: summary, findings, and a source list
  (`app/agents/research_report.ResearchReport`/`Finding`/`Source`, JSON-serializable both ways -- an agent's JSON
  reply parses straight into it, and it serializes straight back out). A finding's `confidence` is derived from
  whether it has a resolvable `source_id`, never taken at the agent's own word: a finding with no source is
  `unverified` (`ResearchReport.unverified`). Persisted as `app/agents/models.ResearchSession` (status
  running/completed/failed, `findings_summary`, `source_references`, the full report JSON, `cost_usd`,
  `duration_seconds`), linked to the underlying `AgentSession` that ran the prompt.
- **Pipeline entry point (implemented, task 43).** `ResearchAgent` is not only reachable standalone (as above) --
  `RESEARCH` is now its own pipeline step type (docs/PIPELINE_ENGINE.md §23), executed by
  `PipelineEngine._h_research`, a sibling of `_h_agent`. It assembles the question through the same
  `assemble_effective_prompt(..., role="RESEARCH", agent_type=...)` call AGENT steps use (so project context,
  skills and `${...}` variables resolve identically, and task 56's local-model support applies for free),
  records the effective prompt into `execution_prompts` the same way, then constructs a `ResearchAgent(adapter,
  db=self._db)` and drives it exactly as described above. The completed report is additionally indexed in the
  Artifact Library as `kind='research_report'`, linked to the step -- the "Artifact Library indexing" this
  section used to describe as task 43/44 follow-up. Task 44 (a backlog research action, implemented --
  `app/backlog/research.py`, docs/SPRINT_PLANNING_AND_BACKLOG.md §50) and task 47 (Ralph failure-trigger wiring,
  implemented) build on this pipeline step and its artifact/execution_prompts shape; task 48 (the knowledge base)
  still does not.
- **Redaction (implemented, task 56).** `ResearchAgent` masks secrets in the report itself -- `summary`, every
  `Finding.text`, every `Source.excerpt` -- before it is ever stored, using the same pipeline applied to Run
  commands/logs/replies (`app/runs/security.redact`: built-in secret-pattern rules plus whatever
  `AGENTFLOW_REDACT_PATTERNS` adds, threaded into `ResearchAgent(extra_patterns=...)` the same way
  `PipelineManager`/`RalphOrchestrator` thread it). Unlike Run artifacts (which keep an unredacted command in
  memory so a redacted step can restart), there is no unredacted copy of a research report anywhere: it is
  redacted at the point it is parsed (`ResearchAgent.collect()`), so the same redacted object is both what a
  caller sees and what lands in `research_sessions`.
- **Limits (implemented).** `ResearchAgent.research()` takes `time_limit_seconds`/`cost_limit_usd`, the same
  shape as Ralph's `max_iterations`/`max_runtime_seconds` (docs/RUN_AND_RALPH.md §22): a limit paired with a
  clear terminal state, stopping the session (or refusing to treat it as a normal completion) once tripped.
  `time_limit_seconds` is strictly enforced by wall clock (`TIMED_OUT`). `cost_limit_usd` is enforced
  (`COST_LIMIT_EXCEEDED`) but is deliberately **best-effort only**: it is checked against whatever `cost_usd`
  figure the adapter itself reports in its session usage metadata, both while a session is still `RUNNING` (an
  async adapter, checked on each poll) and once it has completed (every adapter today -- Codex, Claude, Fake --
  resolves synchronously within `start()`, so this is the path that matters in practice). There is deliberately
  no per-model $/token rate table anywhere in this codebase, and none is planned: adapters shell out to CLIs
  rather than calling provider APIs directly, rates drift, and a token-based cost estimate would be
  unmaintainable decorative code for no real benefit today. A session whose adapter never reports real cost
  telemetry is bounded only by `time_limit_seconds` -- token-based estimation is out of scope until a real need
  justifies it.
- **Locking (implemented).** `ResearchAgent.research()` never calls `app/projects/lock.py`: research holds no
  exclusive project lock (RUN_AND_RALPH §22), so it can run alongside other work.
- **Advisory output.** A person accepts findings before they change a plan or acceptance criteria (unchanged by
  Phase A: nothing here writes to a plan or acceptance criteria automatically).

### 23.1 Knowledge base indexing (wiki-style; proposed)

The shared knowledge base must be quick to search and easy to browse, for agents and for people. Assumptions:

- **Wiki pages.** Every entry has a stable `slug`, a title, aliases (`py`, `python3`), tags, a one-paragraph
  `summary` written for scanning, and a body. Entries link to each other with `[[slug]]`; links are parsed on save
  into a link table, giving backlinks and a "related" list. Renames keep the old slug as an alias.
- **Progressive disclosure.** An agent should not load whole pages to find one. It reads in three steps, cheapest
  first: (1) the *index*, a compact generated table of contents by topic, language and library (one line per
  entry: slug, title, summary); (2) the entry's summary and headings; (3) the full body or a single section.
- **Search.** SQLite full-text search (FTS5) over title, aliases, tags, summary and body, ranked, with filters
  for kind (`note` / `web_cache`), language, library, version, confidence, freshness and scope. Results return slug,
  title, summary and snippet, never whole bodies. Exact slug and alias hits rank first.
- **One interface for agents.** `search(query, filters)`, `get(slug, section?)`, `list_topics()`, `related(slug)`
  and `propose(note)`. Exposed to the CLI agents as a skill-style bundle (a generated `SKILL.md` describing the
  commands plus the current index file) and, where an adapter supports it, as tools. Reads are logged so use counts
  and "what was consulted" appear in the report; unused, stale or duplicate entries surface for tidy-up.
- **Freshness in the index.** Each index line and result shows fetched-at / reviewed-at and expired or stale
  marks, so an agent can tell a current fact from an old one before relying on it.
- **Scope-aware.** The index and search only return `global` entries and those of the current project
  (see sharing rules above).
- **For people.** The same index is browsable in the UI as a wiki: topic tree, page view with backlinks, search box,
  recent and most-used lists, and a "needs review / stale" queue.

### 23.2 Agent Capability Delivery Architecture (decided task 51)

**Decision: Layered Skill Management with Prompt Injection**

Agent capabilities and extended context are delivered via prompt injection, with AgentFlow serving as the Master Data Management (MDM) source for skill definitions and context assembly.

**Architecture:**

1. **Generic Skills Layer (AgentFlow MDM)**
   - Reusable capabilities common across multiple projects (e.g., "how to test a web UI", research patterns, common workflows).
   - Stored and versioned in AgentFlow (database + files under `app/agents/skills/` or similar).
   - AgentFlow is the single source of truth; generic skills are synced to project files when an agent session starts, or injected directly into prompts.
   - Validated regularly by review agents.

2. **App-Specific Skills Layer**
   - Project or repository-specific capabilities (e.g., custom testing harnesses, deployment procedures).
   - TBD: stored in AgentFlow and synced to repo, OR persisted in repo only and pulled by AgentFlow.
   - Open question for follow-up: trade-offs between centralized (AgentFlow) vs. decentralized (repo-only) storage for app-specific skills.

3. **Role-Based Context Injection**
   - Skills are adapted and injected into prompts based on agent role (RESEARCH, PLANNING, IMPLEMENTATION, VERIFICATION, GENERAL).
   - Prompt library (task 30) assembles role-specific context at session start.
   - Same skill pool serves all roles; context varies by role's needs.
   - No agent discovery or parsing required; agents receive a complete, tailored prompt.

**Rationale (vs. alternatives):**

- **Rejected: Per-agent skill files (approach a).** Skill files (e.g., `CLAUDE.md`, `SKILL.md`, per-adapter) would scatter skill definitions across agent repos, prevent reuse across projects, and require each adapter to parse custom formats. AgentFlow would have no MDM and no way to validate or version skills consistently.

- **Rejected: Pure MCP server (approach b).** An MCP tool server adds latency for each skill invocation, requires MCP client support in every agent (not all agents may have it), and complicates adapter implementation. MCP is better suited for runtime tool access than for upfront context delivery.

- **Selected: Prompt injection with layered skill management (approach c).** AgentFlow assembles tailored prompts upfront, including all role-specific context. Agents receive complete, reviewed guidance without discovery overhead. All skill definitions are versioned and validated in AgentFlow. Token consumption is the same as other approaches when skills are actually used (during invocation), but upfront assembly is simpler. Recorded `execution_prompts` (task 30) provide full visibility for compliance and debugging.

**Evaluation Against Design Criteria:**

- **Adapter uniformity (AGENT_ADAPTER §11):** All agent-specific logic is confined to adapters; the prompt/context assembly layer is agent-agnostic. Skills are defined once in AgentFlow and delivered the same way to all adapters.
- **FakeAgent testability:** FakeAgentAdapter receives the final assembled prompt, no discovery or parsing needed. Test fixtures can be deterministic and self-contained.
- **Token cost:** No net difference when skills are invoked compared to discovery approaches. Upfront token cost for injected context is offset by simpler adapter logic and no discovery overhead.
- **Recorded effective prompts (task 30):** Full prompt + injected context are recorded together in `execution_prompts`, providing end-to-end visibility.
- **Interactive vs. pipeline sessions:** Same mechanism works for both; context is assembled at session start, enabling consistent behavior across use cases.
- **Redaction (app/runs/security.py):** Injected skill context is part of the final prompt and is redacted before persistence, same as other prompt content.

**Fallback to Native Agent Capabilities:**

AgentFlow's skill management is the primary path, but adapters retain a fallback to native agent discovery:

1. **Primary (recommended):** AgentFlow assembles and injects role-specific skills into prompts at session start.
2. **Secondary fallback:** If skill injection fails, is unavailable, or AgentFlow's skill service is down, adapters degrade gracefully:
   - Agents can still discover and use their native instruction files (e.g., `CLAUDE.md` in the project, Codex's native config).
   - Project-level context files (task 34) are still available as a fallback layer.
   - The session works, but without AgentFlow's validated, role-adapted skills.

This two-tier approach ensures resilience: centralized management for optimal behavior, but agents remain functional without AgentFlow's skill layer.

**Open Questions & Deferred Decisions:**

1. **App-specific skill storage (Decided: centralized with fallback):** App-specific skills are defined in AgentFlow (alongside generic skills) and synced to project repos on session start, or made available to adapters during context assembly. This provides:
   - Single source of truth for skill definitions and versioning.
   - Consistent validation and review across all app-specific skills.
   - Projects still retain native fallback (task 34, project context files) if needed.
   - Implementation task should detail sync strategy and fallback behavior.

2. **Skill format and metadata:** Define the schema for skill definitions (title, description, when to use, constraints, dependencies). Should skills reference each other or have dependencies?

3. **Skill versioning and updates:** How do generic skills versions propagate? On every session start, or on-demand? Should projects pin skill versions or always use latest?

4. **Skill review and validation:** How often do review agents audit skill definitions? What triggers a review (time-based, change-based, usage-based)?

**Follow-Up Tasks:**

- Task: "Design and implement skill management service in AgentFlow" — models, persistence, versioning, sync/retrieval.
- Task: "Create initial set of generic skills" — research patterns, testing strategies, common workflows.
- Task: "Implement role-based context injection in prompt library (task 30)" — adapt skills per agent role.
- Task: "Integrate skill review agents" — periodic audits and validation of skill definitions.
- Task: "Decide app-specific skill storage strategy" — resolve centralized vs. decentralized storage trade-offs.
- Task: "Surface skills in agent prompts and session UI" — ensure agents see available capabilities and can discover them if needed.

### 23.3 Skill management implementation (task 52)

Task 52's own scoping text (a separate `Skill`/`SkillVersion`/`SkillDependency`/`AgentSkillBinding` model
set with its own persistence, disk storage, REST API and cache) predated this decision and duplicated
machinery §23.2 already delegates to the prompt library. It was implemented as an **extension of
`app/prompts/`, not a parallel system**:

- A skill is a `prompt_fragments` row with `skill_status` set (`draft` / `active` / `deprecated`; empty
  means "an ordinary fragment, not a skill"). It reuses fragment content, tags, and per-content-change
  versioning (`prompt_fragment_versions`, now with a `changelog` column) rather than a second model.
- Skill-only fields on the fragment: `skill_when_to_use`, `skill_constraints` (advisory, free text — not
  a machine-evaluated constraint DSL), `skill_depends_on` (other skill fragment ids, cycle-checked the
  same way template inheritance is), `skill_roles` and `skill_adapter_types` (empty list = applies to
  everything), `skill_priority`, `skill_author`.
- `AGENT_ROLES` (`app/agents/base.py`) is a plain tuple (`GENERAL`, `PLANNING`, `IMPLEMENTATION`,
  `RESEARCH`, `VERIFICATION`) — the first time this repo names its roles as anything other than free
  strings threaded through adapter `options`. Everything that already passed an ad hoc role string keeps
  working; this only validates/filters skill bindings.
- `app/prompts/assembler.skill_context(db, role, agent_type, project_id)` selects active skills matching
  role/adapter type, orders them (dependencies before dependents, then priority), and renders them.
  It never raises — a broken skill row logs a warning and assembly continues without it. Passing
  `role`/`agent_type` to `assemble_effective_prompt(...)` appends this automatically.
- Wired into every prompt-building call site: pipeline AGENT steps and Ralph iterations (already called
  the assembler), the sprint planning agent (`PlanningAgent` now takes `db` and injects `PLANNING`-role
  skills), and — the one real gap this closed — interactive chat session start
  (`app/sessions/routes.py`), which previously never touched the prompt library at all and sent a bare
  `"Starting session..."` placeholder. `app/agents/models.PLACEHOLDER_PROMPT` matching was changed from
  an exact match to `.endswith(...)` so a skill-prefixed placeholder still doesn't get used as the
  session's auto-derived title.
- Disk storage (`app/prompts/skill_sync.py`) mirrors skills as JSON (not YAML — the codebase has no YAML
  dependency anywhere else) under `app/agents/skills/<adapter-type-or-"generic">/`, seeded idempotently
  at startup the same way `seed_defaults` seeds templates/blocks. This is a generated, reviewable mirror;
  the database stays the source of truth. Syncing skills into project repositories remains the deferred,
  separately-tracked follow-up ("Decide app-specific skill storage strategy").
- UI lives in the existing `prompts` blueprint (`/prompts/skills`), following the same AJAX/`_reply()`
  convention as fragments/templates/blocks — no separate public REST API, matching the rest of the app
  (no external API consumers, no auth/admin concept to gate on).

**(2026-09-28, task 53) Closed: compliance/audit trail for role-based injection.** Task 53's own scoping
text asked for a separate `RoleContextAssembler` with its own fallback-to-native-discovery step; that
machinery was already rejected in favour of `skill_context()` above (§23.2's "graceful degradation" —
a broken or unreachable skill source just returns `("", [])`, so the agent's own native discovery, e.g.
reading a repo's `CLAUDE.md`, still runs independently and needed no explicit "invoke fallback" step).
The one genuine gap was that `Assembled.metadata()`/`skill_context()`'s skill list was computed and used
to build the prompt, then discarded, at three of the four call sites — never written anywhere queryable.
Closed by:
- `execution_prompts.skills_included` (new column, alongside the existing `blocks_included`): pipeline
  AGENT steps and Ralph iterations now pass `assembled.skills_included` / the skills used for that
  iteration's prompt into `record_prompt()`.
- `agent_sessions.metadata["injected_context"]` (`{role, agent_type, skills, assembled_at}`, via the
  existing `update_session_metadata()` merge — no new column): interactive chat session start
  (`app/sessions/routes.py`) and the sprint planning agent (`app/sprints/planning_agent.py`) both start
  an ordinary `AgentSession` rather than a pipeline step or Ralph iteration, so they had no
  `execution_prompts` row to record against; the session's own metadata is the natural place, matching
  how it already carries other per-session facts (e.g. `repo_id`).
- The research agent (`app/agents/research_agent.py`, task 45) selects `RESEARCH`-role skills the same
  way but was left untouched here (out of scope for this pass); it has the same gap and would want the
  same `injected_context` treatment.
Every role-based injection site now has a durable, queryable record of which skills were actually
selected for a given session or step.

### 23.4 Initial generic skill content and review cadence (task 54)

Task 54's own scoping text proposed a second parallel model set — `app/skills/governance.py`'s
`SkillReviewSchedule`/`SkillReviewTemplate`/`SkillAuditLog`, `app/skills/proposals.py`'s
`SkillProposal` — for content and process that §23.3 already put inside `app/prompts/`. Same trap as
§23.3 itself, one level up. Not built; instead:

- **Content.** Eight generic skills (`app/prompts/defaults.DEFAULT_SKILLS`) are seeded active by
  `models.seed_builtin_skills()`: one `RESEARCH`-role skill grounding answers in the repository
  context `RepositoryContextLoader` actually assembles (docs/AGENT_ADAPTER.md §23, Phase A); two
  `IMPLEMENTATION`/`VERIFICATION` testing skills (mobile/desktop viewport conventions from this file's
  own "Temporary UI and Mobile Rules"; this repo's pytest/`FakeAgentAdapter` scripting conventions);
  two `VERIFICATION`/`GENERAL` code-review checklists (correctness; security, scoped to this app's
  actual attack surface — no auth, host command execution, path handling — rather than generic
  OWASP boilerplate); one `IMPLEMENTATION` skill grounding Ralph failure diagnosis in the exact
  fields the retry prompt is built from (docs/RUN_AND_RALPH.md §22 "Failure evidence"); one
  `IMPLEMENTATION` skill on this codebase's own sqlite3-without-an-ORM persistence conventions. A
  web/knowledge-base search-strategy skill was deliberately **not** written: docs/AGENT_ADAPTER.md §23
  Phase B (shared knowledge base) and Phase C (web) are proposed, not built, so a skill describing them
  would describe capability that doesn't exist. A deployment/CI-CD skill was likewise skipped — this
  app has no deployment pipeline of its own to ground one in.
- **Seeding is deliberately not automatic under `TESTING`.** `seed_defaults`/`seed_skills_from_disk`
  already run unconditionally in `create_app()`, but several `tests/prompts/` tests assert an *empty*
  skill set on a freshly created app (e.g. `test_skill_context_empty_when_no_skills`). Rather than
  rewrite that documented invariant, `seed_builtin_skills()` is called from `create_app()` guarded on
  `not app.config.get("TESTING")` — the same guard already used a few lines below for
  `ralph_manager.resume_automatic_sprints()` — so a real dev/prod instance gets the built-in skills at
  startup (idempotently: an existing name is never overwritten) while the test suite's database stays
  exactly as empty as before. `tests/prompts/test_skill_seed_content.py` calls `seed_builtin_skills()`
  directly to exercise the content itself.
- **Review cadence.** `prompt_fragment_versions.changelog` (§23.3) already answers "when did this
  skill's *content* last change, and why" — that needed no new machinery. It cannot express "a person
  looked at this and it's still correct", which a content-change-only history has no row for. Two new
  columns close that gap: `skill_last_reviewed_at`, `skill_reviewed_by` (both on `prompt_fragments`,
  `models.mark_skill_reviewed()`, `POST /prompts/skills/<id>/reviewed`) — updated independently of
  `version`, so marking a skill reviewed never fabricates a content-change history entry. Cadence
  itself is a **documented convention, not enforced**: review a skill when its `skill_when_to_use`
  stops matching how an adapter/role is actually used (change-triggered), or at least once a quarter
  for an `active` skill with no review in that window (time-triggered) — the `skills.html` list plus
  each skill's "Last reviewed" line is the whole mechanism for spotting either; no scheduler, no
  automated audit job. "Approves" a change means a person edits or deprecates the skill fragment
  directly (`update_fragment`/`deprecate_skill`) or clicks "Mark reviewed" — this app has no
  authentication or admin-role concept to gate that on (§23.3's own note), so `skill_reviewed_by` and
  `skill_author` are both free text, not an account reference.

## 24. MCP Tool Bridge (Backlog/Sprints/Pipelines/Ralph)

An interactive `GENERAL`-role chat session (§5) is, by default, confined to editing code in its
project's working directory: it has no way to read or write Backlog items, Sprint tasks, Pipeline
executions or Ralph runs. Grooming and planning conversations naturally start in chat, so a session
may opt into a small set of MCP tools that call directly into those subsystems -- the same
persistence/workflow functions the UI itself uses, so validation (backlog status transitions,
readiness gates) and audit history (`backlog_triage_history`, sprint approval history) behave
identically whether a person clicked a button or an agent called a tool. This is a **direct-write**
bridge, not a proposal queue like `PlanningAgent` (§below) -- writes take effect immediately.

- **Opt-in per session, off by default.** The session-start form (`app/templates/sessions/project.html`)
  has an "mcp_tools" checkbox; `create_session` (`app/sessions/routes.py`) passes it into
  `adapter.start(context, initial_prompt, options={"mcp_tools": ...})`. Each adapter persists it on the
  session's `metadata["mcp_tools"]` (alongside `model`) so `resume()`/`send()` keep declaring the MCP
  server on later turns without the caller re-specifying it every call.
- **One Python subprocess per turn, scoped to one project.** `app/mcp/server.py` (`python -m
  app.mcp.server --project-id N`) is spawned by the CLI itself, not by Flask -- MCP servers speak stdio
  JSON-RPC to their parent process. `AGENTFLOW_DATABASE_PATH`/`AGENTFLOW_MCP_SESSION_ID` reach it as
  subprocess environment variables, the same mechanism `ClaudeAdapter._local_model_env` uses for
  `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`. The project id is fixed at process startup, not a
  per-call tool argument: every tool in `app/mcp/tools/` re-validates that the item/task it's given
  belongs to that project (mirroring the 404-on-mismatch check `app/backlog/views.py`'s `_item()` does
  for the UI), so a confused or compromised agent cannot address another project's data even though the
  wrapped persistence functions take a bare `item_id`.
- **`PYTHONPATH` must be set explicitly in the server's own env, not just AgentFlow's.** Confirmed as a
  real production failure, not a theoretical one: the CLI's (and therefore the MCP server child
  process's) working directory is `AgentContext.working_directory` -- the *target project's* repository
  (e.g. `/home/devadmin/git/SAM6`), never AgentFlow's own install directory. `python -m app.mcp.server`
  run with that cwd cannot find the `app` package and the CLI reports the server as `"status":"failed"`
  in its own init event -- silently, with no AgentFlow-visible error, leaving the agent to improvise
  with whatever other tools it has (observed: it edited the target repo's own `.taskmaster/tasks/tasks.json`
  and called that "adding to the backlog"). `mcp_config.server_command()` sets
  `env["PYTHONPATH"] = <repo root>` (computed from `__file__`, not `sys.path`/cwd at call time) so the
  module lookup works regardless of the spawning CLI's cwd. Testing this requires actually spawning
  from a different cwd (`tests/agentflow_mcp/test_server_integration.py` runs from the repo root, which
  would not have caught this) -- verify any future change here live, cwd'd into a *different* project's
  repository, not just via the `mcp` SDK's own test client from AgentFlow's own directory.
- **Declared to the CLI differently per adapter** (`app/agents/mcp_config.py`), since the two CLIs read
  MCP server declarations differently:
  - Claude reads `--mcp-config <path>`; the helper writes a small, session-stable JSON file
    (`{"mcpServers": {"agentflow": {"command": ..., "args": [...], "env": {...}}}}`).
  - Codex has no such flag; the same declaration goes in as `-c mcp_servers.agentflow.*` inline
    overrides -- the same dotted-TOML-path `-c` mechanism `CodexAdapter._model_flags`/
    `_permission_flags` already use for `model_providers.local.*`/`sandbox_mode`, so no config file is
    needed for Codex.
  - Both `--mcp-config` and Claude's `--allowedTools` (below) are **variadic** flags in their CLI's
    argument parser -- passed as two separate argv entries (`["--mcp-config", path]`) they silently
    swallow the prompt positional that follows as another value of the same flag, the identical
    failure mode `CodexAdapter._image_flags` already guards against for `--image`. Both must be one
    glued `--flag=value` token; confirmed live against the installed `codex`/`claude` binaries, not
    just the SDK's own test client.
- **Declaring the server is not enough -- each tool must be explicitly granted, separately from the
  session's file-edit/shell permission mode.** Confirmed against real `codex`/`claude` binaries, not
  just `mcp` SDK-level testing: a session in Claude's `acceptEdits` mode still gets `"Claude requested
  permissions to use mcp__agentflow__backlog_create_item, but you haven't granted it yet"`, and a
  Codex session with a plain `sandbox_mode` override still gets `"user cancelled MCP tool call"` (no
  terminal to answer an interactive approval prompt in non-interactive `codex exec`). Both adapters
  grant the tools:
  - Claude: `--allowedTools=mcp__agentflow__<tool>,...` (`mcp_config.claude_allowed_tools_flag()`),
    scoped to exactly `mcp_config.TOOL_NAMES` -- extend that tuple, not the flag-building code, as
    Sprint/Pipeline/Ralph tools are added. Everything else (Edit, Bash, ...) still follows whatever
    `--permission-mode` the session picked; this grant touches nothing beyond AgentFlow's own tools.
  - Codex: `--approve-for-me` (`mcp_config.codex_mcp_flags()`). Codex has no per-tool or per-server
    allowlist reachable from `codex exec`/`-c` overrides (only interactive `codex mcp add` persists a
    server as trusted, which doesn't fit a one-shot non-interactive turn scoped to a single project).
    `--approve-for-me` routes approval requests through Codex's own automatic review under the
    `workspace-write` sandbox -- narrower than `--dangerously-bypass-approvals-and-sandbox` (which
    disables sandboxing entirely) but broader than Claude's exact-tool grant: it also auto-reviews any
    shell-command approval the turn would otherwise have needed, not just the MCP call. This is a
    real, session-wide behaviour change from enabling `mcp_tools` on a Codex session, not just an
    additive capability -- worth keeping in mind if a Codex session's selected sandbox mode was meant
    to hold the line on what it can do without asking.
- **Tool catalog wraps existing functions; no new business logic.** `app/mcp/tools/backlog.py` wraps
  `app/backlog/persistence.py` (create/list/get/update/transition/record_note/list_history --
  attachments are excluded, binary upload doesn't fit a JSON tool schema). Sprint, Pipeline and Ralph
  tool modules follow the same wrapping pattern against `app/sprints/`, `app/pipelines/`, `app/ralph/`
  and `app/sprints/queue.py` and land in the same phased order they were built: Backlog first (lowest
  blast radius, no locking), then Sprints, then Pipelines/Ralph (which must go through
  `PipelineManager.start()`/`RalphManager.start()` rather than the engine/orchestrator directly, so
  `project_locks` (docs/RUN_AND_RALPH.md §19) is still respected).
- **Attribution.** Tool calls pass `created_by`/`changed_by` as `"agent:<session_id>"`, so triage and
  approval history distinguish an agent-driven change from a person's UI action without a new column.
- **Thread safety.** The `mcp` SDK runs each synchronous tool handler via a worker-thread pool rather
  than one fixed thread; `app/mcp/db.ConnectionPool` hands out one sqlite3 connection per thread
  (opened lazily), the same fix `HostExecutionProvider._db()` (`app/execution/host.py`) uses for the
  identical problem.
- **Testing.** `tests/agentflow_mcp/test_backlog_tools.py` covers the wrapped functions directly (unit,
  including the cross-project-boundary rejection); `tests/agentflow_mcp/test_server_integration.py`
  spawns the real `app/mcp/server.py` subprocess and drives it with the `mcp` client SDK end to end.
  `FakeAgentAdapter` gained a `tool_call` script step (`app/agents/fake.py`) that calls the same
  `app/mcp/tools/` functions in-process, so `options["mcp_tools"]` -> tool-call wiring has a
  deterministic test path with no real CLI subprocess required (`tests/test_sessions.py`).

## 25. Chat-Orchestrated ALM Flow

Decided in discussion (not yet implemented; depends on the still-unbuilt Sprint/Pipeline/Ralph tool
modules from §24 and on SPRINT_PLANNING_AND_BACKLOG.md §51's Design/test-definition action). A
`GENERAL`-role interactive chat session with `mcp_tools` enabled can be asked to carry a feature or
requirement through the whole ALM flow in one conversation: create the Backlog item, research it,
define its tests (§51), add it to a Sprint, select or define that Sprint's pipeline, then execute it.
The chat session is the orchestrator, but every step is a tool call into the AgentFlow MCP server, not
something the chat session's own agent does by hand (editing files, running shell commands itself)
-- this keeps validation and audit identical to a person doing each step through the UI, and keeps
AgentFlow's own persistence/workflow functions, not the chat agent's judgment, as the source of truth
for what actually happened at each step.

Two distinct execution shapes, which every future Sprint/Pipeline/Ralph tool must pick correctly rather
than defaulting to one:

- **Blocking, summary-returning steps.** Research, Design (test definition, §51) and Sprint planning
  each already spawn a bounded sub-agent (`ResearchAgent`/`PlanningAgent`/§51's design action)
  synchronously and return, the same way they already do when triggered from the UI
  (`DEFAULT_TIME_LIMIT_SECONDS=300`-class bound, §23 "Limits"). Their MCP tool wrappers block for the
  same duration and hand the result straight back to the chat session in the same turn -- this is the
  shape that lets the orchestrating session say "research found X, Y, Z; defining tests next" without a
  second round trip. Open risk, not yet verified: whether the actual Claude/Codex CLI's own MCP
  tool-call timeout tolerates a multi-minute blocking call -- needs confirming against the real
  binaries (same "confirmed live, not just SDK-level" discipline §24 already applies to its other MCP
  claims) before this shape is relied on for anything longer than Research's existing bound.
- **Fire-and-forget steps.** Pipeline execution and Ralph runs can run far longer than any reasonable
  tool-call timeout and are already async in the UI (`PipelineManager`/`RalphManager` background
  threads, status polled separately, never blocked on). Their MCP tools must match that: start the
  execution and return an id immediately, with a separate status-checking tool for the chat session (or
  a person re-opening the conversation later) to poll -- never a `manager.join()`-style blocking call
  from inside a chat tool handler.

**Summary shape.** Every blocking tool's result is a compact digest -- title, top findings or proposals,
counts -- plus the record's id (`research_session_id` / proposal ids / criterion ids), not the full
report inlined. A separate `*_get_report`-style tool retrieves full detail only if the chat session (or
the person watching it) actually asks for it, mirroring how the Backlog UI already shows a condensed
research summary with full detail only in the expanded view (§50). Rationale: inlining a full report
into every orchestration step would bloat the orchestrating chat session's own context on every single
call, most of which the person will never ask to see in full.

**Open questions.** Not yet decided: how the chat session is meant to discover "no pipeline exists yet
for this Sprint, define one" versus selecting an existing definition -- a read-only
`pipeline_list_definitions`-style tool fits the existing wrapping pattern, but *authoring* a new
definition from a chat conversation is a materially bigger capability than selecting one, and may not
belong in v1; and how a fire-and-forget execution re-enters the conversation once it finishes -- does
the chat session poll a status tool in a loop within its own turn (bounded by its own tool-call/turn
budget), or does whoever is watching have to prompt again later to check.
