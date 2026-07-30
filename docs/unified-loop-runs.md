# Unified loop runs

**Status:** scoped and agreed; not implemented. Decisions recorded below are
signed off — Option B for storage, typed `source`, no legacy runs to migrate.

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

#### Option A — dual-write when a source is present (rejected)

Keep the manifest as the story read path. One store that writes SQL always
and the manifest additionally when `source` is set.

- No migration; existing manifests keep working.
- Keeps two copies of run state that must agree. The `push_at` bug in the
  run-005 repair is the shape of failure this invites: two writers, one
  stale.
- The unified command layer has to stay aware of the source to know whether
  to dual-write — the coupling we are trying to remove, moved down a layer.

#### Option B — SQL canonical, manifest holds identifiers only (chosen)

**In SQL (`loop_runs`)** — all run state, for every run:

- existing columns unchanged: `run_key`, `work_slug`, `target_kind`,
  `target_ref`, `definition_id`, `definition_revision`,
  `definition_snapshot`, `status`, `current_step_id`, `state`, timestamps,
  lease columns
- the `state` blob stays the single source of truth for `loop` (stages,
  reports, `pr`, `pr_comments`, `pr_config`, review gate, findings), plus
  `brief`, `run_kind`, `entry_stage_id`, `source_run_id`, `workspace_path`
- `artifact_id` / `plan_run_id` become the generic `source` — the only
  provenance a unified run carries. Typed, not free text: a
  `LoopRunSourceKind` enum (`story` today) plus the reference, so adding a
  future trigger is an enum member rather than a second migration. `NULL`
  source = a sourceless run (Loop mode)

**In the manifest (`~/Atelier/works/<WRK>/planning/manifest.json`)** — provenance
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

- **`service._runs` rewrite** (`service.py:588`) — build `PlanArtifactRun`
  from `loop_runs` filtered by source instead of from the manifest. This is
  the bulk of the work.
- **Breaks the on-disk manifest shape.** Signed off. Two facts make it cheap:
  there is exactly one story run in existence (`run-005`, DI migration,
  finished), and nothing outside this repo reads `artifact_runs`. So no
  migration script is required — the shape change lands with the code, and
  `run-005` can be left as-is or reduced by hand.

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

1. ~~**Worktree slug for story-sourced runs** — stable `loop-<artifact_id>`.~~
   **Done.** `loop_actions.sourced_worktree_slug`, passed from
   `start_run._launch_initial_agent`. `ensure` / `ensure_forked` are both
   idempotent on an existing target, so a later run attaches instead of
   forking. Nothing deletes worktrees on run end (cleanup releases runtimes
   only), so sharing one across runs is safe.
2. ~~**`source` on the run entity** — nullable, replacing
   `target_kind`/`artifact_id`/`plan_run_id`.~~ **Done, derived not stored.**
   `LoopRunSourceKind` + `LoopRunSource` in `domain/loop/dtos.py`, exposed as
   a `LoopRunRecord.source` property that reads the existing columns. No new
   SQL: a stored copy could disagree with `target_kind`/`artifact_id`, which
   is the duplicate-state problem Option B exists to remove. The columns get
   replaced in step 3, where the shape changes anyway. `main.py` now branches
   on `source` instead of `target_kind`.

   `workspace_path` (populated for sourceless runs, never for story runs) was
   scoped here but deferred: it is computed at the HTTP layer from
   `WorkspacePaths`, so populating it from the command means a new dependency
   for a field nothing reads yet. Do it in step 4/5 when the unified store
   needs it.
3. ~~**Storage migration (Option B)** — manifest reduced to ids,
   `service._runs` reads SQL.~~ **Done.** `artifact_run_rows` reads run state
   from `loop_runs` filtered by `source.ref`; `PlanningLoopRunStore`,
   `start_run` and `mark_run_cleaned` write SQL only. The manifest keeps
   `{artifact_id: [run_id, ...]}` via `record_artifact_run_id`, which also
   normalises any manifest still holding run bodies so the list cannot end up
   a mix of dicts and strings. `artifact_runs_for_update` is deleted rather
   than left to hand id strings to callers expecting dicts.
4. ~~**One command module, and drop the `objective` prefix.**~~ **Renames
   done; the module merge is partial.** Extract the
   target-agnostic parts of `objective_runs.rerun` (amend-brief construction,
   task/review stage selection, reusable-status guard) into `domain/loop/`,
   then collapse the seven planning command modules into it. Renames:
   - `commands/loops/objective_runs.py` → `commands/loops/runs.py`
   - `domain/loop/objective_start.py` → `domain/loop/start.py`
   - `domain/loop/objective_store.py` → `domain/loop/store.py`
   - `ObjectiveLoopRunStore` + `PlanningLoopRunStore` → one `LoopRunStore`
   - `ObjectiveStartSpec` → `LoopRunStartSpec`;
     `ObjectiveRunRequest` → `LoopRunRequest`
   - `ObjectiveRunNotFound` / `ObjectiveWorkNotFound` /
     `ObjectiveContextMissing` → delete; the generic `LoopRunNotFound` and
     friends already exist
   - `OBJECTIVE_TARGET_ID` → delete (a sourceless run needs no target id);
     `OBJECTIVE_WORKTREE_SLUG` → `DEFAULT_WORKTREE_SLUG`, still `"loop"`
5. **One route family** — `/works/{slug}/runs/...` for everything, with
   `source` in the create payload. **Still open, but no longer blocking
   anything.** Its hard half was said to be unifying the two start paths:
   `commands/planning/start_run` hardcoded `definition.stages[0]` when it
   launched the first agent and rendered its prompt, while `loop/start.py`
   resolved an entry stage and handled an entry needing no agent at all
   (a deterministic check).

   Rather than merge two ~300-line functions, the *decision* they disagreed
   on is now shared: `followups.resolve_entry` and
   `followups.entry_needs_agent`, called by both. A story run can start
   partway through a loop today, so what remains here is deduplicating the
   route pairs -- housekeeping against future drift, not capability.

   The fork-from-parent-agent seeding that looked like this step's main risk
   is **gone**: it served the superseded handoff flow, its only caller always
   passed `null`, and handoff itself forks through `POST /works/{slug}/agents`.
   Both paths now do the same thing with a workspace -- plain `ensure` from a
   root.
6. ~~**Delete the `variant` gates.**~~ **Done.** Story runs have both
   follow-up kinds: `POST /plan/artifacts/{id}/runs/{run_id}/rerun`,
   `commands/planning/rerun_run.py`, and
   `run_kind` / `follow_up_note` / `entry_stage_id` on
   `StartArtifactRunRequest`. The chooser is ungated in `LoopRunView`, and
   `variant` now survives only for the header back arrow, which is genuinely
   planning-only.

   `retry-stage` needed nothing: it is `lifecycle.resume(retry_failed=True)`,
   which `planning/resume_run` already accepted, so story runs have had it
   all along.

Steps 1-4 and 6 have landed. Step 5's remaining half -- collapsing the two
route families into one -- is deduplication now that both paths share the
entry-stage decision and the storage model. Worth doing to stop them drifting
again, but nothing depends on it.

## Decisions

- **Storage: Option B.** SQL canonical, manifest keeps run ids only.
- **`source` is typed** — enum discriminator + reference, not free text.
- **No legacy migration.** One story run exists and it is finished; nothing
  outside the repo reads `artifact_runs`.
- **Existing worktrees are not migrated.** Finished runs keep the per-run
  worktree they were built in; the stable slug applies to new runs.

## Related asymmetry

`workspace_path` is populated on objective runs and always `None` on story
runs. It does not break the within-run feedback pass — the monitor resolves
the worktree from the stage agent's `worktree_slug` (`monitor.py:1173`), not
from that field — but it is the same split and should be unified in step 2.
