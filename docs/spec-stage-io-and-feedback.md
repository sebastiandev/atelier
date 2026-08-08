# Spec — Stage inputs, feedback, and pass history

Status: draft for review · Author: session 2026-08-07 · Supersedes nothing yet

## 1. Why

Every loop defect this week came from the same place: **half of what a stage
receives is declared and validated, and half is injected by the orchestrator
under ad-hoc suppression rules.**

Declared (`stage.context`) is composable and validated. Injected is not:

```python
# monitor.py — the injected half, abridged
resolution_note = "\n\n".join(v for v in (
    pass_feedback.context(run),
    "" if stage.kind.value == "pr" else pr_lifecycle.pending_feedback_context(run),
    resolution_note.strip(),
) if v)
# ...and elsewhere
if corrective and corrective in resolution_note:   # substring de-dup
    corrective = ""
if PREVIOUS_REPORT not in context_kinds:           # fallback only when undeclared
    stage_row["corrective_note"] = corrective
```

Concrete failures traced to it:

| Symptom | Cause |
|---|---|
| Review agent never saw why tests were deleted | `pass_feedback` held one note; a second request overwrote the first |
| Run cycled 4 passes, pushing each time | PR feedback clears only when the PR stage *passes*; the PR stage wouldn't pass while feedback was open |
| Sending PR feedback failed the run | A guard couldn't distinguish an agent's `changes_requested` from the user's synthesised one |
| Reviewer re-raises findings the user dismissed | `waived_findings` is recorded, exposed as a *count*, and rendered into no prompt |
| Can't give a reviewer two reports | `declared_previous_row` returns `next(...)`; the prompt carries one report triple |

The through-line is that these are all *modelling* gaps, not bugs. This spec
closes them.

## 2. What a stage receives today

### 2a. Declared — `stage.context`, each `required` or `optional`

| Kind | Content |
|---|---|
| `target` | Artifact / source ref |
| `plan_index` | Plan tree |
| `artifact_dependencies` | Upstream artifacts |
| `workspace_diff` | Full live diff |
| `changed_files` | Changed-file list from prior reports (cumulative in the pass) |
| `previous_report` | Summary + findings + validation evidence; `step` may name a stage |
| `files` / `folder` | Paths |
| `note` | Free text |
| `shared_context` | Shared folder |

### 2b. Injected — always present, no declaration

| Input | Source | Lifetime | Suppression |
|---|---|---|---|
| Stage instructions | definition | — | always |
| Scope contract | `prompts.py` | — | always |
| Brief note + context | run brief, per stage | run | always |
| `pass_feedback` | approval / review send-back | until accept | always |
| `pending_pr_feedback` | selected PR comments + note | until PR stage passes | skipped for PR stages |
| `corrective_note` | prior `changes_requested` report | stage row | only when `previous_report` undeclared |
| Caller note | retry note, gate instruction, feedback summary | one-shot | substring de-dup |
| PR setup bundle | `pr_config` | run | PR stages only |
| Report contract | `prompts.py`, branches on stage kind | — | always |

### 2c. Feedback — five shapes, three lifetimes, two routing styles

| Trigger | Stored as | Lifetime | Routes via |
|---|---|---|---|
| Approval → request changes | `pass_feedback` | until accept | stage's `changes_requested` edge |
| Review gate → send back | `pass_feedback` + report findings | until accept | review's edge |
| PR feedback | `pending_pr_feedback` | until PR stage passes | synthesised report on PR stage |
| Retry note | not stored | one attempt | same stage |
| `corrective_note` | stage row | one stage | — |

## 3. Target model

### 3a. Inputs are declared, all of them

Everything in §2b that varies by stage moves into the declaration. Undeclared
input is **not rendered** — no suppression rules, no substring de-dup.

```yaml
inputs:
  - kind: target              required: true
  - kind: workspace_diff      required: true
  - kind: waived_findings     required: false
reports:
  - from: implementation      required: true
  - from: previous            required: true
history: none                 # none | summaries | full
```

### 3b. `reports` replaces the single `previous_report`

- `from:` is a stage id or the symbolic `previous`.
- Duplicates collapse: if `previous` resolves to `implementation`, it renders once.
- Each renders as a labelled block — `Report — implementation (pass 4)` — so the
  agent knows whose account it is reading.
- Validation error when `from:` names a stage that cannot have run first.
- **`corrective_note` is deleted.** It exists only as a fallback for stages that
  don't declare `previous_report`; with reports explicit there is nothing to
  patch around.

### 3c. `history` is an independent axis

| Level | Content | Cost |
|---|---|---|
| `none` | nothing | — |
| `summaries` | one line per stage per pass (see below) | flat |
| `full` | every stage report of every pass, verbatim | linear, unbounded |

`full` is kept — it is the right setting when an agent genuinely needs the whole
record — but production stages use `none` or `summaries`.

**A summary line must carry substance.** `pass 4 — review: changes requested
(2 findings)` tells an agent nothing it can act on, and an ambiguous one-liner is
worse than no history because it invites guessing. Each line is:

```
pass 4 — implementation: restored the N:M order validation and its command tests
pass 4 — review: changes requested — timeline op-type parity; error-equivalence
         assertions asserted only the exception class
pass 5 — you asked: don't import kernel tables from app tests  (answered)
```

- The stage's own `summary` field, which the report contract already requires to
  be "a short verdict a reader takes in at a glance".
- For `changes_requested`, the finding gists appended — not just a count.
- Feedback lines quote the request and mark whether it was answered.

If a summary is empty or useless the line says so explicitly
(`no summary reported`) rather than rendering a bare outcome.

### 3d. `waived_findings`

Keeps its current name. A **run-wide** list of findings the user chose not
to act on, rendered as its own section:

```
## Already dismissed by the user — do not raise again
- Kernel unnest writes UNNEST operation types where legacy writes null
```

Written on **every** dismissal path, not just partial send-back:

| Path | Today | Target |
|---|---|---|
| Review gate, subset enforced | non-enforced findings recorded | unchanged |
| Review gate, `approve_as_is` | nothing recorded | **all reported findings recorded** |
| Run accepted with open findings | nothing recorded | **all open findings recorded** |

Consumers: any stage declaring the input (reviewers), and the approval surface,
so the user is reminded what they already dismissed before approving again.

### 3e. One feedback record

```
Feedback:
  id, created_at, pass_number
  source:       approval | review | pr_comment | pr_general | retry
  items:        [{ ref, author, location, body, instruction }]
  note:         str
  scope:        attempt | open
  target:       restart | retry
  answered_by:  <stage id>            # the gate that closes it
  state:        open | answered
```

One store, one renderer, one routing rule. The five existing shapes collapse:

| Old | `scope` | `target` | `answered_by` |
|---|---|---|---|
| Approval request-changes | `open` | `restart` | that approval stage |
| Review gate send-back | `open` | `restart` | that review stage |
| PR feedback | `open` | `restart` | the code review stage |
| Retry note | `attempt` | `retry` | the stage it was sent to |
| `corrective_note` | *deleted* — it is `reports` | | |

`target` defaults by stage kind (review/approval → `restart`, check → `retry`)
and is overridable per stage via `on_feedback`.

Two details this table originally got wrong, corrected against the Phase 1
implementation:

- **A retry note does carry `answered_by`.** The draft left it blank. It has to
  name the stage the note was sent to, because that is what `close_attempt`
  keys on to end the record when the attempt reports.
- **PR feedback falls back to the PR stage when the loop has no review.** The
  table assumes a code review exists. `atelier-fast` has none, and an
  unanswerable record would be worse, so `_verifying_stage_id` falls back.
  **This is load-bearing for Phase 4**: the fallback only avoids the §1
  cycling defect because a PR stage is currently denied open feedback by
  `monitor._open_feedback_for`. Removing that `stage.kind` stopgap without
  giving the PR stage a declaration that withholds feedback re-creates the
  deadlock this spec exists to close.

### 3f. Answering

**Feedback is answered when the gate that would verify it passes** — not when
the run is accepted.

- Review returns `pass`, or the gate is resolved `approve_as_is` → every open
  record whose `answered_by` is that stage becomes `answered`.
- `approve_as_is` additionally writes the reported findings to
  `waived_findings` (§3d).
- **Accepting the run** answers every open record and writes their findings to
  `waived_findings` too: accepting *is* the user saying the result is good as it
  stands. A later run against the same work therefore starts knowing what was
  already let through.
- Answered records remain visible as history (§3g); they are never deleted.

This is what removes the deadlock structurally: clearing is an explicit
transition driven by the verifying stage, not a side effect of one particular
stage returning one particular outcome. A later PR update that attracts new
comments opens *new* records; the old ones stay answered.

### 3g. Prompt zones

```
## What has already happened          ← narrative, past tense, never imperative
   pass 3 — implementation: <summary>
   pass 3 — review: changes requested (2 findings)
   pass 4 — you were asked to …  (answered)

## Already dismissed by the user — do not raise again
   - …

## Your task now                      ← the only imperative section
   <stage instructions>
   <open feedback, newest first>
```

Rules:

1. **Only `open` feedback is an instruction.** Answered feedback drops to
   history verbatim. This is what removes contradictory instructions: today an
   answered request still reads as a live order.
2. **Cap and roll up.** Beyond **4 passes**, older history collapses to one line
   per pass regardless of the `history` level. Hardcoded, not configurable: it is
   a rendering safeguard, not a knob worth exposing.
3. **One source per fact.** A finding appears in history *or* in open feedback,
   never both.

## 4. Blast radius

### Backend — domain

| File | Change |
|---|---|
| `loop/dtos.py` | `Feedback` DTO; `reports`/`history`/`inputs` on `LoopStepDefinition`; `waived_findings` kind; `on_feedback` |
| `loop/feedback.py` *(new)* | Record store: `open`, `answer`, `context`, `dismissed` — replaces `pass_feedback.py` |
| `loop/pass_feedback.py` | Deleted, callers migrated |
| `loop/prompts.py` | Zone restructure; multi-report rendering; waived section; history roll-up |
| `loop/monitor.py` | Orchestrator: build inputs from the declaration only; answer feedback on gate pass; drop `corrective_note` and the suppression rules |
| `loop/lifecycle.py` | `resume` / `request_changes` / `accept` write `Feedback`; `_resume_prompt` uses the shared renderer |
| `loop/pr_lifecycle.py` | `prepare_feedback` opens a `Feedback` record; `pending_feedback_context` and the clear-on-pass coupling removed |
| `loop/actions.py` | `declared_previous_row` → resolve a list |
| `loop/definitions.py`, `loop/stages.py` | Validate `reports` / `inputs` / `history`; ordering checks |
| `loop/stage_builtins.py`, `loop/builtins.py` | Built-ins declare their inputs and reports |
| `loop/snapshots.py`, `loop/transport.py`, `loop/persistence.py` | Definition snapshot round-trip, import/export |

### Backend — application & infrastructure

| File | Change |
|---|---|
| `http/schemas.py` | Stage definition schema; `waived_findings_count`; feedback shapes |
| `http/routes/stages.py`, `routes/loops.py` | Stage/loop CRUD carry the new fields |
| `http/routes/works.py` | Run surface exposes open vs answered feedback and dismissed findings |
| `infrastructure/filesystem/stage_definitions.py`, `loop_definitions.py` | On-disk shapes |
| `domain/commands/loops/runs.py`, `commands/planning/*` | Feedback verbs go through the new store |

### Frontend

| File | Change |
|---|---|
| `LoopUI.tsx` | **Loop editor**: inputs/reports/history editors per stage; `on_feedback`; the context editor becomes the inputs editor |
| `LoopRunView.tsx` | Feedback panels read one record type; open vs answered; waived-findings view |
| `LoopRunInspector.tsx` | Show a stage's declared inputs and resolved reports |
| `LoopBriefSetup.tsx` | Per-stage brief unchanged, but validation follows the new required/optional |
| `PlanningMode.tsx`, `WorkView.tsx` | Pass the new run fields through |
| `api.ts` | Types for all of the above |

### Docs

`docs/backend.md` (stage IO + feedback model), `docs/architecture.md` if a new
port appears, `docs/api-flows.md` for the feedback endpoints.

## 5. Keeping current loops working

**No dual fields, no dual strings, no version branches in business code.** The
old shape is normalised into the new one at a single reader boundary, and
everything downstream only ever sees the new model.

### 5a. The one seam

`loop/snapshots.py` (`definition_from_snapshot`) and the filesystem definition
readers are the only places that know two shapes exist. Each applies defaults:

| Old | Normalised to |
|---|---|
| `context: [previous_report(step: X)]` | `reports: [{from: X, required}]` |
| `context: [previous_report]` (no step) | `reports: [{from: previous, required}]` |
| `context: [other kinds]` | `inputs: [same kinds]` |
| no `history` | `history: none` — matches today, where no history is rendered |
| no `on_feedback` | default by kind (review/approval → `restart`, check → `retry`) |
| no `waived_findings` input | absent — opt in explicitly |

A loop that never declared `previous_report` gets `reports: []`, which is what
it effectively had: the `corrective_note` fallback it used instead disappears
with it, so such a stage loses the prior findings unless it declares a report.
That is a real behaviour change and the reason built-ins are updated in the same
phase.

### 5b. What is *not* carried over

| Surface | Decision |
|---|---|
| `pass_feedback`, `pending_pr_feedback` on live runs | Read as empty. New runs only |
| `corrective_note` on stage rows | Ignored, then deleted |
| `waived_findings` | Unchanged — same key, same REST field |
| DB schema | No change; everything lives in `state` JSON |

Runs in flight when this ships keep their pinned snapshot and finish under the
normalised reading. Their feedback records start empty, so an open request may
need re-sending once — acceptable, and the same trade already taken.

## 6. `stage.kind` — what survives

Today the domain branches on `stage.kind` in **31 places**. Most are not
mechanics; they are declarations in disguise. After this work they fall into
three groups.

### 6a. Deleted — becomes declaration

| Site | Today | Replaced by |
|---|---|---|
| `prompts.py:137` | report contract per kind | `stage.report_contract`, which already exists as a field |
| `monitor.py:1196`, `start.py:238` | `persona = architect if review else developer` | `stage.persona` |
| `monitor.py:1258-1263`, `lifecycle.py:603-605`, `start.py:264` | prompt type + PR bundle per kind | `inputs` / `report_contract` |
| `monitor.py:1071-1082`, `lifecycle.py:332,371` | changes-requested handling per kind | `on_feedback` |
| `_without_a_pr_send_back` | PR may not send back | declared outcomes |

### 6b. Kept, but as dispatch — genuinely different execution

A deterministic check runs no agent; a user approval parks the run and waits; a
PR stage publishes. These are *execution strategies*, not attributes, and the
repo's own rule ("variant-heavy concepts use `singledispatch`") applies:

```
run_stage(AgentStage | CheckStage | ApprovalStage | PrStage, ...)
```

Sites: `monitor.py:280,728,843,857,868`, `lifecycle.py:164`. Five branches
become four registered implementations, and the orchestrator stops naming kinds
altogether.

### 6c. Kept as-is — definition validation

`definitions.py:110,165,176,195` and `stages.py:88,91` enforce per-kind schema
("a PR stage needs `pr_config`", "a check needs a command"). Validation is
exactly where a type-driven rule belongs; it stays, ideally dispatched too.

**Target: zero `stage.kind` checks in `monitor.py` and `lifecycle.py`.** That is
the measurable outcome of this section.

## 7. Phasing

| Phase | Work | Ships value |
|---|---|---|
| **1** ✅ | `Feedback` record + store; migrate the five writers; `answered_by` on gate pass; `waived_findings` incl. `approve_as_is` | Deadlock gone; reviewer stops re-raising dismissed findings |
| **2** | `inputs` / `reports` / `history` declaration; delete `corrective_note` and the suppression rules; validation | Composable stages; two-report reviewers |
| **3** | Prompt zones + roll-up | Prompt pollution fixed |
| **4** | `on_feedback` per stage; `run_stage` dispatch (§6b); loop-editor UI for all of it | Configurable routing; zero kind checks in the orchestrator |

Phase 1 is where the defects live. Phases 2–3 are what stop them recurring.

**Phase 1 landed** in `domain/loop/feedback.py` (`pass_feedback.py` and
`pending_pr_feedback` deleted). Two rules are keyed on `stage.kind` as a
deliberate stopgap and become declarations in phase 2: a PR stage receives no
open feedback (`monitor._open_feedback_for`), and only a review stage is shown
the dismissed findings (`monitor._waived_for`). See the §3e note on why the
first of those cannot simply be deleted. `corrective_note` is untouched; it
goes with `reports` in phase 2.

Waiving on approval happens in `lifecycle.accept`, on both the terminal and the
continuing branch, because the decision is the user's and the run may still
overwrite `loop["findings"]` with a later stage's report before it ends.

## 8. Decisions

None outstanding. Settled during review:

- `waived_findings` keeps its name and is **run-wide**.
- No backward-compatible field duplication: old shapes are normalised at one
  reader boundary (§5).
- History roll-up is hardcoded at **4 passes**.
- **Accepting a run waives its open findings**, like `approve_as_is` does.
- `history: full` stays; `summaries` must carry the report's substance, not a
  bare outcome and a count.
