---
name: sdd-feature
description: Use when starting any new feature, architecture, integration, ability, item, or system in this project - walks the spec → plan → marker contract → implement → evidence → landmine loop so nothing ships on "it compiles"
---

# SDD feature loop

Every feature in this codebase moves through six gates, in order. Skipping one
is how silent engine failures ship. 

## 1. Spec

Create `docs/specs/<NNN>-<feature-name>/spec.md` from
`docs/specs/spec-template.md`. State what the user experiences, not how the
code is shaped. Every value with a number (timeout, limits, etc.) gets a source: cite where it came from, or
tag it `DESIGN-FRESH` so nobody later mistakes an invention for research.

## 2. Plan

Add `plan.md` beside it: files to touch, order of work, and a **Global
Constraints** section listing the invariants from `AGENTS.md` that this feature
could plausibly violate. Read `AGENTS.md`'s Architecture invariants NOW, not
after something breaks.

## 3. Marker contract - BEFORE implementing

Add `contracts/log-markers.md`: the exact `[MARKER]` strings the project will
print to prove the feature worked, and the failure patterns that must NOT
appear. The test-suite rig greps for these verbatim. Writing the contract first means
implementation and verification agree on paper, not by accident.

## 4. Implement

- Execute the checklist.
- You must adhere to the **Abstract First** rule: Never hardcode a third-party service directly into the business logic.
- Always route through an interface (e.g., `BaseSTT`, `BaseTTS`, `BaseDataStore`) before writing concrete integrations like Groq, Kokoro, or Firestore.

## 5. Evidence

"It compiles" and "tests pass" are not done. Done is:

- Individual unit test passes,
- the e2e harness prints every
  marker from the contract and no failure patterns,
- for anything visual: a frame - screenshot or extracted video frame - showing
  the user would have seen it. A log line proves code ran; only a frame
  proves it rendered.

## 6. Document & Landmines

If the engine surprised you at any point - silent failure, wrong docs,
behavior, WebRTC WebSocket jitter issue, a Firebase Auth edge case, or a misaligned LLM JSON payload that cost more than ten minutes to explain - append it to
`AGENTS.md`'s Architecture invariants before closing the feature. 
Update inline documentation and the overall API specs/README. 
A landmine not written down will be stepped on again.
