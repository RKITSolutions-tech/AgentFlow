# AgentFlow Execution Provider Design

## 1. Purpose

This document defines how AgentFlow executes processes on the host or inside Docker while presenting a consistent interface to agents, pipelines, tests, terminals and runtime services.

## 2. Execution Abstraction

All execution should pass through an ExecutionProvider interface.

```python
class ExecutionProvider:
    def available(self): ...
    def create_context(self, configuration): ...
    def execute(self, command, options): ...
    def start_process(self, command, options): ...
    def stop_process(self, process_id): ...
    def signal_process(self, process_id, signal): ...
    def stream_output(self, process_id): ...
    def process_status(self, process_id): ...
    def destroy_context(self, context_id): ...
```

## 3. Initial Providers

```text
HostExecutionProvider
DockerExecutionProvider
```

## 4. Host Execution Provider

Executes directly on the same Linux host as AgentFlow.

Responsibilities:

- working directory
- environment
- process groups
- stdout/stderr
- timeout
- termination
- PTY support
- ownership tracking

## 5. Docker Execution Provider

Executes inside Docker.

Initial modes:

```text
existing container
ephemeral Run container
```

Later:

```text
Docker Compose service
workspace container
remote Docker host
```

## 6. Execution Context

ExecutionContext represents the environment in which commands run.

```text
id
provider
target
working_directory
environment_profile
mounts
network
created_at
status
```

## 7. Working Directory and Filesystem Boundaries

Every process has an explicit working directory. Project and repository path validation must prevent accidental traversal outside permitted roots.

## 8. Environment

Environment resolution may use:

```text
system
provider
Project
Sprint
Run
step
```

Sensitive values should not be persisted in normal logs.

## 9. Process Entity

```text
id
provider
context_id
external_process_id
command_summary
working_directory
status
started_at
completed_at
exit_code
```

States:

```text
CREATED
STARTING
RUNNING
STOPPING
STOPPED
COMPLETED
FAILED
LOST
TIMED_OUT
```

## 10. Process Groups

Processes started by AgentFlow should belong to controllable process groups where possible. Stopping a Run should not leave orphan child processes.

## 11. Streaming

stdout/stderr are streamed into the event system. Large output should also be stored efficiently as log artifacts rather than duplicated repeatedly in database rows.

## 12. Timeouts

Execution may define:

```text
startup_timeout
execution_timeout
idle_timeout
shutdown_timeout
```

## 13. Cancellation

Cancellation should distinguish graceful termination, forced termination and provider/context destruction.

## 14. PTY Support

PTY is required for:

- interactive shell
- persistent terminal
- some agent CLIs
- interactive programs

PTY is a provider capability rather than something each consumer reimplements.

## 15. Persistent Terminal

Persistent browser terminals should use tmux or a similar host-side persistence mechanism.

Host:

```text
Browser
→ WebSocket
→ Terminal Manager
→ tmux
→ shell
```

Container:

```text
Browser
→ WebSocket
→ Terminal Manager
→ provider bridge
→ Docker exec/container shell
```

The exact container persistence mechanism may differ.

## 16. Provider Capabilities

Providers should report capabilities such as:

```text
pty
streaming
signals
persistent_process
filesystem_mounts
network_config
container_lifecycle
```

Consumers must not assume every provider supports every capability.

## 17. Host Security

Controls should include:

```text
allowed roots
environment filtering
process ownership
timeouts
audit logging
optional command policy
```

## 18. Docker Security

Controls should include:

```text
approved images
mount restrictions
network configuration
resource limits
container cleanup
Docker socket access policy
```

## 19. Resource Limits

Future controls may include CPU, memory, process count, disk and runtime limits.

## 20. Recovery

On AgentFlow restart, stored process state should be reconciled with observed state.

Example:

```text
stored: RUNNING
observed: process missing
result: LOST
```

A lost process marks its active Iteration and owning Run `BLOCKED`. Resuming requires an explicit recovery or restart decision.

## 21. Events

```text
ExecutionContextCreated
ProcessStarted
ProcessOutput
ProcessCompleted
ProcessFailed
ProcessTimedOut
ProcessStopped
ExecutionContextDestroyed
```

## 22. Phase 1 Scope

Phase 1 prioritises the CloudCLI replacement:

```text
HostExecutionProvider
process execution
PTY
streaming
persistent terminal
agent CLI execution
basic runtime commands
```

Docker support may be introduced late in Phase 1 if needed for immediate use.

## 23. Phase 2 Scope

Phase 2 expands:

```text
full DockerExecutionProvider
ephemeral Run containers
Docker Compose rigging
resource limits
recovery hardening
external execution providers
```
