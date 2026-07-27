# Unified loop runs

**Status:** proposal. Nothing implemented. Scoping only — no code has been written against this.

A loop run is a loop run. Where it was triggered from — a freeform objective,
or a plan story — is provenance, not a different kind of thing. Today it is
modelled as two kinds, and every story-run bug this session came from that
seam: missing approval buttons, missing Create PR, permanently-empty PR
panels, missing follow-up runs. Each was the story path lacking something the
objective path had.

Proposal: one run entity with a nullable `source`. Empty in Loop mode; set to
the story when Planning triggers it.

## What is already shared

The loop engine is not duplicated. Both paths converge on
`loop_monitor.execute`, `loop_actions.initialized_loop_snapshot`,
`pr_lifecycle.add_one_off_stage`, `pr_lifecycle.capture_completion`, and
`pr_review.refresh`. `initialized_loop_snapshot` already takes
`entry_step_id` and skips earlier stages (`domain/loop/actions.py:25-41`) —
the mechanism a `verify` follow-up needs exists and is shared.

## What is duplicated

**Routes** — two families in `application/http/routes/works.py`:

| operation | objective | story |
|---|---|---|
| list runs | `:360` | `:1803` |
| get run | `:402` | `:1831` |
| create run | `:458` | `:1708` |
| resume | `:549` | `:1862` |
| request-changes | `:672` | `:1943` |
| accept | `:732` | `:2014` |
| create-pr | `:784` | `:2091` |
| pr-refresh | `:420` | `:1531` |
| pr-feedback | `:839` | `:2160` |
| cancel | `:901` | `:2236` |
| cleanup | `:930` | `:2277` |
| **retry-stage** | `:613` | **absent** |
| **rerun (amend/verify)** | `:959` | **absent** |

Story-only operations: none. The story family is a strict subset.

**Commands** — objective keeps one module (`commands/loops/objective_runs.py`);
story spreads the same operations across seven (`start_run`, `resume_run`,
`request_run_changes`, `accept_run`, `pr_runs`, `cancel_run`,
`mark_run_cleaned`).

**Schemas** — `WorkLoopRun*` mirrors `PlanArtifactRun*` throughout.

## The two real obstacles

These are not plumbing. They need decisions.

### 1. Worktree allocation differs

| | worktree slug | lifetime |
|---|---|---|
| objective | `OBJECTIVE_WORKTREE_SLUG = "loop"` (`domain/loop/objective_store.py:16`) | **stable per work** |
| story | unset → defaults to `agent.slug` (`domain/agents/launch.py:146`) | **new per run**, forked from parent |

Later stages inherit the first agent's slug in both cases
(`domain/loop/monitor.py:1173`), so stages share a worktree *within* a run.
Across runs they do not, for stories.

Consequence: **the follow-up promise does not hold for stories today.** "Keeps
the same branch; an open pull request is updated in place"
(`FollowUpChooser`, `frontend/src/LoopRunView.tsx:1197`) depends on the stable
worktree that only objective runs have. Porting `rerun` without fixing this
would fork a new worktree and open a second PR.

Fix: give a story-sourced run a stable slug derived from its source, e.g.
`loop-<artifact_id>`. One worktree per story rather than per run. This changes
on-disk layout for new runs and needs a decision on existing ones (leave
them; they are already per-run and finished).

### 2. Canonical storage differs

This is the dual-write question.

| | canonical | mirror |
|---|---|---|
| objective | `loop_runs` row (`ObjectiveLoopRunStore.load` → `get_objective_run`, `objective_store.py:80`) | none |
| story | `manifest["artifact_runs"][artifact_id]` (`PlanningLoopRunStore.load`, `loop_store.py:31-38`; `service._runs`, `service.py:594`) | `loop_runs` row, **write-only** |

The story SQL row is written by `persist_artifact_run`
(`domain/planning/loop_persistence.py:13`) and read by exactly one consumer:
boot-time monitor resumption (`main.py:307-318`), which uses only
`work_slug`, `artifact_id`, `plan_run_id`. Nothing reads the `state` blob
back. The API's story runs are built from the manifest, not SQL.

So today the same run state exists twice, with the copies canonical in
opposite directions depending on kind.

#### Option A — dual-write when a source is present

Keep the manifest as the story read path. One store that writes SQL always
and the manifest additionally when `source` is set.

- No migration; existing manifests keep working.
- Keeps two copies of run state that must agree. The `push_at` bug in the
  run-005 repair is the shape of failure this invites: two writers, one
  stale.
- The unified command layer has to stay aware of the source to know whether
  to dual-write — the coupling we are trying to remove, moved down a layer.

#### Option B — SQL canonical, manifest holds identifiers only (recommended)

**In SQL (`loop_runs`)** — all run state, for every run:

- existing columns unchanged: `run_key`, `work_slug`, `target_kind`,
  `target_ref`, `definition_id`, `definition_revision`,
  `definition_snapshot`, `status`, `current_step_id`, `state`, timestamps,
  lease columns
- the `state` blob stays the single source of truth for `loop` (stages,
  reports, `pr`, `pr_comments`, `pr_config`, review gate, findings), plus
  `brief`, `run_kind`, `entry_stage_id`, `source_run_id`, `workspace_path`
- `artifact_id` / `plan_run_id` become the generic `source` — the only
  provenance a unified run carries

**In the manifest (`.atelier/planning/<WRK>/manifest.json`)** — provenance
only:

```json
"artifact_runs": { "bug-typebuilder-hardening-gaps": ["run-005"] }
```

A list of run ids per artifact. No `loop`, no stages, no PR state.

Why: the manifest lives in the user's repo and is meant to describe the
*plan* — which stories exist, their source hashes, what has been attempted.
Run execution state is machine state: large, high-churn, rewritten on every
monitor tick. It has no business in a file the user may commit, and keeping
it there is what forces the dual write.

Rationale for the direction: the manifest copy is what the story UI reads
today, but the SQL copy is already written on every save and is already
canonical for the identical objective state. Making SQL canonical removes a
copy rather than adding one.

Costs:

- **Migration.** For each `artifact_runs[artifact_id]` entry, ensure a
  `loop_runs` row exists carrying its `state`, then reduce the manifest entry
  to its id. Forward-only, idempotent, `scripts/migrate-*.py` pattern
  (`scripts/migrate-transcripts.py` is the template). Rows are already
  written for every story run, so in practice this is a verify-then-shrink.
- **`service._runs` rewrite** (`service.py:588`) — build `PlanArtifactRun`
  from `loop_runs` filtered by source instead of from the manifest.
- **Breaks the on-disk manifest shape.** Per `AGENTS.md` this needs explicit
  sign-off: users with existing plans have run state in their manifests that
  moves into SQL. The migration is what makes it safe; without it, story run
  history disappears from the UI.

## Naming

The entity vocabulary is already generic and correct: `LoopRun`,
`LoopRunRecord`, `LoopRunTarget`, `LoopRunRepository`, `LoopRunStatus`,
`LoopRunStateStore`, and the `loop_runs` table. Nothing to rename there.

"Objective" exists only in the kind-specific layer this proposal deletes
(~180 occurrences): `objective_runs`, `ObjectiveRunRequest`,
`ObjectiveLoopRunStore`, `ObjectiveStartSpec`, `OBJECTIVE_TARGET_ID`,
`OBJECTIVE_WORKTREE_SLUG`, `objective_start`, `objective_store`,
`objective_run_key`, and the `Objective*` error classes -- most of which
already have generic twins (`LoopRunNotFound`, `LoopRunNotResumable`).

So this is a deletion plus one promotion:
`commands/loops/objective_runs.py` becomes the generic
`commands/loops/runs.py`, and `ObjectiveLoopRunStore` +
`PlanningLoopRunStore` collapse into one `LoopRunStore`.

Two cautions:

- **"Objective" survives only as the freeform goal text** in Loop mode,
  which the code already calls `goal` (`ObjectiveStartSpec.goal`,
  `target_ref`). Keep "goal"; drop "objective" as an entity or mode word.
  After unification the distinction is *sourceless* vs *story-sourced*.
- **The source is not a "kind."** `LoopRunKind` is taken and means
  `initial | amend | verify`. Keep `source` distinct from it.

## Sequencing

Each step is independently shippable and leaves the tree green.

1. **Worktree slug for story-sourced runs** — stable `loop-<artifact_id>`.
   Unblocks everything else; on its own it makes story PRs update in place.
2. **`source` on the run entity** — nullable, replacing
   `target_kind`/`artifact_id`/`plan_run_id`. Additive in SQL.
3. **Storage migration (Option B)** — manifest reduced to ids, `service._runs`
   reads SQL.
4. **One command module** — extract the target-agnostic parts of
   `objective_runs.rerun` (amend-brief construction, task/review stage
   selection, reusable-status guard) into `domain/loop/`, then collapse the
   seven planning command modules into thin wrappers.
5. **One route family** — `/works/{slug}/runs/...` for everything, with
   `source` in the create payload. Old story routes become aliases, then are
   removed.
6. **Delete the `variant` gates** — `frontend/src/LoopRunView.tsx:353`, `:483`.
   `retry-stage` and `rerun` become available to story runs for free.

Steps 1-2 are safe and useful on their own. Step 3 is the one that needs the
compat decision. Steps 4-6 are mechanical once 1-3 land.

## Open questions

- Do existing per-run story worktrees need migrating to the stable slug, or
  is "new runs only" acceptable? (Recommend the latter; finished runs keep
  their workspace for history.)
- Should `source` be a typed union (`story` today, others later) or just a
  nullable artifact reference? Typed costs nothing now and avoids a second
  migration if loops ever get triggered from something else.
- Does anything outside the backend read `artifact_runs[*].loop` from the
  manifest — scripts, the user's own tooling? Not found in this repo, but the
  file is in the user's repo and reachable from outside it.
