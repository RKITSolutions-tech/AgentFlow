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
