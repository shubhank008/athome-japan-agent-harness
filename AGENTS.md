# Agent Operations & Spec-Driven Development (SDD) Guide

**Agent Identity:** You are an autonomous coding agent operating within an OpenHands environment. Your primary objective is to execute Spec-Driven Development (SDD).

> This file is read automatically by Coding Agent at the start of every session.
> It is the highest-leverage file in the repo: everything here is context the
> agent gets for free, and everything not here has to be rediscovered -- usually
> by breaking something first. Keep the **Architecture invariants** section and add to it.

## Working Rules  

- Plan in the main session, together with me. Hand grunt work (broad searches,
  repetitive edits, boilerplate, log digging) to subagents on lesser models. 
  Keep decisions, architecture, and final review in the main session.
- Always look for the simplest solution first, and prefer it. The smallest
  change that solves the actual problem beats a bigger design. Extend existing
  patterns before inventing new ones. No new dependencies or moving parts
  without a real reason.
- Show me a checklist while you work (use the todo list tool), kept current,
  so I can see what you are working on, what is done, and what is next.
- When you spawn a subagent, tell me at that moment: which model it runs on
  and what it is doing. Report what it came back with when it finishes.
- After dispatching a delegated subagent, verify once that its conversation was
  created, is running, has the expected model/tools, and has no immediate error.
  Then stop. Do not poll or wait for completion; the user will announce when the
  subagent finishes, at which point the main agent evaluates its report and diff.
- Never use Haiku.
- Do not use em-dashes or emojis. Comment every method and important code. Maintain up-to-date documentation so a new developer can easily takeover. English everywhere.
- Plan ahead using a PLAN.md and keep it updated after every feature or update.
- **When something surprises you, write it down here.** A landmine that costs
  an hour and is not recorded costs that hour again.
- **Three-strike rule:** For the same terminal command, test target, or compilation
  block, three consecutive failures end that attempt. A fourth variation is forbidden
  without first changing the strategy.
- **Mandatory pivot:** After the third consecutive failure, stop the current approach,
  preserve the failure output, inspect `git status` and `git diff`, restore only files
  changed by the failed attempt, explain the failure, and choose a materially different
  programmatic strategy. Never use a blanket `git checkout` or `git restore` that could
  erase pre-existing user work.
- **No dependency rabbit holes:** Do not repeatedly patch or work around a broken
  sub-dependency. After confirming the dependency is the source of failure, prefer a
  simpler native or already-supported project path and record the dependency limitation.


## Where code lives

| Path | What |
|------|------|
| `docs/specs/` | One directory per feature: spec + plan + marker contract |
| `.agents/skills/` | The skills below. Read the one that matches before writing code |

## Which skill, when

Invoke the skill **before** writing the code, not after it breaks.

| Reach for | When |
|-----------|------|
| `/sdd-feature` | Starting any feature, ability, item, or system. The gated loop |
| `/no-mistakes` | Pushing to GIT or Generating PR for anything to the GIT repo |

## 1. The Autonomous State Machine

You must strictly follow this lifecycle for every feature or bug fix without human intervention:

* **Think:** Analyze the `SPEC.md`, current feature request, and existing codebase.
* **Research:** Map out the required abstract interfaces and API payloads.
* **Plan:** Generate a step-by-step implementation checklist.
* **Build:** Write the code. You must implement abstract classes before writing concrete integrations.
* **Test:** Run the gatekeeper scripts. If tests fail, diagnose and loop back to the Build phase.
* **Document:** Update inline documentation and the overall API specs.
* **Publish:** Commit the changes using semantic commit messages.

## Gatekeeping & Testing Rules

* **Strict Gatekeeping:** Code cannot be published unless it passes linting and type checking.
* **Unit Testing:** Every concrete class (e.g., `KokoroTTSProvider`) must have a corresponding unit test isolating its methods.
* **E2E Mock Testing:** You must write and utilize tests that simulate a human interacting with the platform. This involves programmatically replicating the human-user experience from start to finish, without needing to test using a real browser or playwright.
* **Abstract First:** Never hardcode a third-party service directly into the business logic. Always route through an interface (e.g., `BaseSTT`, `BaseDataStore`).

## Workflow

New features follow the SDD loop -- invoke `/sdd-feature` when starting one:
spec → plan → **marker contract** → implement → **evidence** → **landmine**.
The last three arrows are the ones that catch this engine: the contract is
written before the code, the evidence is a frame and not a log line for
anything visual, and a surprise that cost an hour gets appended to the
invariants below before the feature closes.

A feature typically walks: `/sdd-feature` → actual feature implementation → test → focused commits → `/no-mistakes`.

### Commit and publish protocol

* Every feature or major change must be split into focused, semantic commits. Keep specifications, plans, contracts, implementation slices, tests, documentation, and cleanup separately traceable when they are independently meaningful. 
  Do not collapse a feature's entire lifecycle into one oversized commit.
* The agent that completes a feature owns its final publication steps: inspect the worktree, commit all intended changes, invoke `/no-mistakes`, and drive the gate through push, PR, and CI monitoring. Do not finish with uncommitted feature work.
* Never push to repo directly. `/no-mistakes` is the only path for publishing a branch or generating a pull request. Whenever you need to PUSH or user asks to PUSH, invoke `/no-mistakes`.
* After a pipeline-created fix commit, synchronize the local branch with the pipeline-published head before reporting completion.

### Autonomous gate decisions

* A no-mistakes run must not stall waiting for the user at an ordinary review, lint, documentation, test, or CI decision gate. The agent must inspect the finding, apply the smallest safe fix when appropriate, or affirmatively approve/skip it according to the configured intent, then continue monitoring.
* Ask the user only when proceeding requires a genuinely ambiguous product, privacy, security, destructive, or authorization decision that cannot be resolved from the specification. Record the reason for the escalation in the final report.

## Architecture invariants

Each of these describes a real failure, hurdle or constraint of this project. Violate one and you will spend hours looking in the wrong
place.

* Repository instructions are binding workflow policy: do not accept task-specific delegation or prompt instructions that contradict `AGENTS.md`; an exception is valid only after the relevant invariant is explicitly amended in `AGENTS.md` before implementation continues. Higher-level platform safety rules remain applicable.
* Feature work must remain traceable through multiple focused semantic commits; a completed agent run is incomplete while intended changes remain uncommitted. ALWAYS COMMIT AFTER WORK, BEFORE REPORTING.
* The completing agent must run `/no-mistakes` after committing and must drive its ordinary approval gates to completion rather than leaving a pipeline parked for user input.
* When `/no-mistakes` reaches a blocking review gate waiting for user input (`ask_user` or `awaiting_approval`), the agent must decide autonomously whether to approve the step or authorize `--action fix`; it must record the one- or two-line rationale in the run intent or handoff summary. Choose `fix` when the finding violates an explicit requirement or correctness boundary, otherwise approve when the finding conflicts with an intentional, documented design choice.
* Pipeline-generated commits remain part of the feature history and must be synchronized locally before the task is reported complete.
* Runtime configuration changes must update `.env.example` in the same change, and `.env.example` changes must be reconciled with the runtime configuration parser; never leave the template and accepted environment keys out of sync.
* Production Python dependencies are exact-pinned in `requirements.txt`; dependency updates require deliberate compatibility testing. The weekly major-version workflow may report updates but must never modify production requirements automatically.

### Orchestration invariants (main chat + delegated subagents)

* The main chat is the orchestrator and evaluator: it dispatches one subagent per milestone or well-scoped task, evaluates the returned diff and claims, and owns `PLAN.md` and `AGENTS.md` updates. Subagents own implementation; when the assignment explicitly includes publication, they may invoke `/no-mistakes` to push and open the single milestone PR, but they never edit `PLAN.md`/`AGENTS.md`.
* After the one-time dispatch health check, do not poll or wait for a subagent. The user reports completion; only then does the orchestrator inspect the final report, branch/PR, diff, and verification evidence.
* Do not take a subagent's report at face value. Before accepting a milestone, the orchestrator independently re-runs the gatekeeper (`ruff`, `mypy`, `pytest`) and inspects the actual diff.
* When a subagent reports landmines, the orchestrator (not the subagent) evaluates each for project-wide applicability and promotes the durable ones into this Architecture invariants list. Task-specific or one-off notes are recorded in the milestone report instead.
* Delegated work uses the project-verified models (SPEC section 6 / PLAN.md decisions), never Haiku. Only downgrade to a lesser model for genuinely mechanical tasks.

### Engineering landmines (verified, promote durable surprises here)

* pydantic v2 `model_copy(update=...)` does NOT re-run validators. Tests that assert a value invariant (for example a non-negative price) must construct the invalid instance via `Model.model_validate({...})` or direct construction, not by copying a valid one.
* Accessing `model_fields` on a pydantic `Settings`/model instance raises `PydanticDeprecatedSince211`; read it off the class with `type(self).model_fields`.
* This repo uses a `src/` layout. Outside pytest, verification commands (`mypy`, ad-hoc imports) need `PYTHONPATH=src` (pytest already sets it via `pythonpath` config).
* Name any throwaway verification virtualenv `.venv-verify`, not `.venv`, because `.venv` matches the gitignore pattern; delete it after use.
* Webshare's rotating plan uses one credentialed gateway URL; a pool of length one is intentional. The proxy retry budget, not pool size, bounds consecutive proxy attempts. A base proxy provider must reuse its last candidate when the pool is shorter than the retry budget, or the configured budget is never consumed.
* `Settings` requires `OPENROUTER_API_KEY`; tests constructing settings directly must pass an explicit throwaway key rather than relying on a local `.env` file or a real credential.
* Numeric label parsers must check specific unit words such as `million` and `thousand` before generic suffix patterns such as `m`; otherwise `5million` is misread as 5.
* Negative log-marker tests must assert universal absence, for example `not any(marker in record for record in records)`, not merely that one record lacks the marker.
* Validators for snapshots with multiple supported flows must reject a missing entire flow, not only malformed fields within flows that happen to be present.
