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


## 8. Prompt Library (task 30, P2.9)

Implementation decisions made without the owner in the loop; recommendations to confirm.

- **Global, not per project.** Fragments, templates and Ralph instruction blocks are shared
  by every project (`/prompts/...`, a "Prompts" link in the sidebar). Per-project overrides
  are the obvious next step but nothing needs them yet. Storage is plain `sqlite3`
  (`app/prompts/models.py`); tables `prompt_fragments` (+ `prompt_fragment_versions`),
  `prompt_templates`, `ralph_instruction_blocks`, `execution_prompts`.
- **Fragments** have a category (`instruction` | `context` | `example`), tags and a version;
  changing the content bumps the version and keeps the old text; metadata edits do not.
- **Templates** are a `body` (with `${...}` variables and `@path` mentions) plus an ordered
  list of fragments and default variables. `body` was added to the task's fragments-only
  model because the existing built-in prompts (`implement-task`, `verify-acceptance`) are
  plain text. **Inheritance** (`base_template_id`): the most-derived non-empty body wins,
  fragments accumulate base-first without repeats, loops are rejected on save. Deleting a
  fragment used by a template, or a template used as a base, is refused.
- **Assembly** (`app/prompts/assembler.py`, AgentFlow-side only, per §2): template chain ->
  body + fragments -> `${...}` variables -> `@path` mentions and `context_files` globs ->
  (optionally) enabled Ralph blocks. Adapters still receive just the final string, so
  `CodexAdapter` is unchanged (the task asked for it to read the `ExecutionPrompt`; the
  string it is handed *is* that record).
  - Variables: with no resolver a missing `${x}` is left in place and listed in
    `missing_variables` (previews); the engine passes its strict resolver, so an unknown
    `${...}` in a real run still fails the step exactly as before.
  - Files: globs are relative to the repository, `..`/absolute patterns and symlinks that
    leave the repository are skipped and reported, at most 20 files of 64 KiB (truncated
    with a marker) are injected, and the repository must sit under `ALLOWED_PROJECT_ROOTS`.
    `@mentions` are read from the *library text only*, never from substituted variable
    values, so task text cannot pull files into a prompt. `user@example.com` is not a mention.
- **Migration.** `PROMPT_TEMPLATES` and `STANDARD_INSTRUCTIONS` moved to
  `app/prompts/defaults.py` and are seeded at start-up (`seed_defaults`, idempotent, never
  overwrites edits; the block set is seeded only into an empty table so a person can disable
  or reorder without them returning). The old names remain as aliases. The engine and Ralph
  read the library at runtime; a library that was never seeded falls back to the defaults,
  whereas a library with every block *disabled* sends no instructions.
- **Ralph blocks**: on/off, ordered (up/down buttons; no drag and drop), versioned, optional
  `applies_to` agent types (`fake`, `codex`, derived from the adapter class name; blank = all).
- **ExecutionPrompt.** Every AGENT step and every Ralph iteration stores its effective prompt
  (redacted like other stored text, flagged) with template, resolved files, variables used and
  blocks included in `execution_prompts`, linked by `step_executions.execution_prompt_id` and
  `ralph_iterations.execution_prompt_id`. The task text said `Run` and `RalphRun`: manual Runs
  are command-only and have no prompt, and a Ralph run has one prompt *per iteration*, so the
  link lives on the iteration. The step inspector already shows the effective prompt as the
  step input; there is no separate page for the record yet.
- **UI.** `library` (templates and fragments tables, add-fragment form), fragment form with
  version history, template form with fragment checkboxes and a preview that assembles without
  saving (variables to try, optional repository for `@file`), and a Ralph blocks list with
  toggle switches. All mutations are AJAX with JSON replies and non-JS redirects; tables use
  `.table-compact`/`.row-actions`, and controls are 44px on touch.
- Not done: a project-level context-file setting (`CLAUDE.md`/`AGENTS.md` discovery, §2), a
  browsable list of recorded prompts, per-template usage counts, import/export.
