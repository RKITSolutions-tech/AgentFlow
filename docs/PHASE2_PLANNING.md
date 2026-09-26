# AgentFlow Phase 2 Planning Consolidation

## 1. Purpose

Single source of truth for design questions and deferred work carried out of
Phase 1 (Task Master task 7). It audits the open questions in
`AGENT_ADAPTER.md` §22, `TASK_INTELLIGENCE.md` §9 and `DOCUMENT_LIFECYCLE.md`
§14, plus notes flagged during Phase 1 implementation.

Status meanings:

```text
RESOLVED   settled by Phase 1 implementation
PROPOSED   recommended answer, recorded so Phase 2 can start from it;
           needs owner confirmation before implementation depends on it
OPEN       genuinely needs design discussion
```

Every PROPOSED answer below was written without the owner in the loop and
should be treated as a recommendation, not a decision.

## 2. Context Files (AGENT_ADAPTER.md §22)

| Question | Status | Answer |
| --- | --- | --- |
| Where do project-level context files live? | PROPOSED | In the repository (checked in, so they version with the code). A Project setting names the path(s); default discovery looks for `CLAUDE.md` then `AGENTS.md` at the primary repository root. No copy is kept in Project metadata. |
| How are on-demand context files surfaced? | PROPOSED | Phase 2: pre-injected on request. The composer's `@` mention (task 14) already resolves a file into the prompt. An agent-callable lookup tool needs a tool protocol across adapters and is deferred to Phase 3. |
| AgentFlow-side assembly or delegated to adapters? | PROPOSED | AgentFlow-side, always. Adapters receive only the final effective prompt (matches §8 and RUN_AND_RALPH §6 prompt persistence). Native instruction files may additionally be surfaced by an adapter but are never the only mechanism. |

## 3. Task Intelligence (TASK_INTELLIGENCE.md §9)

Phase 1 built no planning, so none of these were resolved by implementation.

| Question | Status | Answer |
| --- | --- | --- |
| Re-running decomposition on an edited Task: merge or replace? | PROPOSED | Merge by default; human-edited subtasks are never discarded. Conflicts show as a per-subtask diff in review. |
| Is dependency rationale kept per edge? | PROPOSED | Yes, permanently, with provenance (human or AI) so reviewers can audit AI-suggested edges later. |
| Does complexity-driven decomposition need more than Planning Profile selection? | PROPOSED | No; fold it into Planning Profile selection. |
| Same adapter/role as implementation, or a PLANNING role? | PROPOSED | A dedicated PLANNING role session. `agent_sessions.role` already exists (default `GENERAL`), so no schema change. |
| May an agent suggest decomposition mid-Run? | OPEN | Clarifying questions exist (task 10, `AGENT_ADAPTER.md` §12) and cover "this is too big, how should I proceed". Whether that becomes a distinct decomposition-suggestion event needs Ralph (P2.5) to exist first. |

## 4. Document Lifecycle (DOCUMENT_LIFECYCLE.md §14)

| Question | Status | Answer |
| --- | --- | --- |
| Version STANDARD documents with a SUPERSEDED chain? | PROPOSED | No: always-current, history left to Git. Only DESIGN/IMPLEMENTATION/TESTING/PLAN chains supersede. |
| Must `review_verdict` name blocking `review_comment` rows? | PROPOSED | The gate is computed from unresolved blocking comments (source of truth). A verdict also records the blocking comment ids as an audit snapshot. |
| Deduplicate `doc_read` events per session? | PROPOSED | No; one row per read. Revisit if volume becomes a problem. |

Section 13 Phase 1 slice status: the `documents` and `document_refs` tables,
the `agent_sessions.document_id` / `reviewed_git_sha` columns
(`app/db.py`) and `app/documents/models.py` exist. The `review_comment`,
`review_verdict` and `doc_read` agent event types are **not yet emitted
anywhere**. That is the only piece of the §13 slice not landed; it is still
unowned and should be picked up with the review loop (P2.4 pipeline wiring),
or as a small standalone task if the manual review workflow is wanted sooner.

## 5. Deferred or Flagged During Phase 1

| Item | Source | Carry to |
| --- | --- | --- |
| Terminal: no idle-timeout kill of abandoned tmux sessions | task 5.4 | Phase 2 hardening |
| Mobile (375px) and desktop (1280px) visual checks written but never run in a real browser here (Playwright unavailable in the dev environment); Runs pages are in the same position | tasks 5.4, 6 | Run the Playwright viewport tests wherever a browser is available before relying on the layouts |
| Runs run command steps only. Prompt/reply columns exist on `run_steps` but nothing writes agent prompts yet | task 6 | P2.5 Ralph |
| Run pause is `SIGSTOP` of the process group, not an agent-level pause | task 6 | P2.5 (steering, pause/resume) |
| A process still alive after an AgentFlow restart cannot be re-attached (its pipes died); the Run is marked BLOCKED once it exits | task 6 | P2.5 or execution-provider work (e.g. output to files instead of pipes) |
| Secret redaction is regex-based and best effort, and line-by-line for process output (a multi-line secret such as a PEM key is only masked where a line matches on its own); a secret with no recognisable shape or key name passes through | task 6 | Ongoing; extend rules via `AGENTFLOW_REDACT_PATTERNS` |
| No parallel Runs, no per-Project concurrency limit | PHASED_DELIVERY_PLAN §4 | Phase 2 |
| Historical replay, artifact library, prompt library | PHASED_DELIVERY_PLAN P2.7-P2.9 | Phase 2 (unchanged) |
| The five Playwright chat tests were reported as hardcoded to port 5000. They already used the `live_server` fixture; the real failure was a hard import error when Playwright is not installed | task 8 | Fixed: they now skip cleanly when Playwright or the browser is unavailable. `tests/test_e2e_sessions.py` still targets port 5000 but is unconditionally skipped |

## 6. Recommended Phase 2 Ordering Changes

None to the P2.1-P2.10 list. Two additions worth scheduling early because
later items depend on them:

1. Context-file resolution (§2) belongs with P2.9 Prompt Library, since both
   feed the same effective-prompt assembly.
2. The §4 document event types belong with P2.4 Pipeline Engine.
