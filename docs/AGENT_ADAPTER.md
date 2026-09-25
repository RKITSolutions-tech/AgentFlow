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

Codex is the first real implementation priority.

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
```

"Other" is part of the tool's UI contract, not an option supplied by the
agent, so AgentFlow must add it itself for every clarifying question. The
structured form is a native Claude Code tool; Codex has no known equivalent.

Open questions to resolve before implementation:

```text
does the AgentAdapter interface need a distinct event/method for
  clarifying questions, or do they reuse the approval event shape with
  an added options list
how does an adapter recognize a clarifying-question event from agent
  CLI output that wasn't designed with structured events in mind (e.g.
  parsing free-form CLI text vs a native tool-call event)
should "other" ever carry AI-suggested prefill text, or always start
  blank
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

## 18. Other Agents

Claude, Gemini and OpenCode should follow after the adapter interface has been proven with FakeAgent and Codex.

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

```text
where project-level context files live (repository file vs Project metadata)
how on-demand files are surfaced (pre-injected on request vs an
  agent-callable lookup/tool)
whether context-file assembly is purely an AgentFlow-side prompt
  composition step, or partly delegated to adapters with native support
```
