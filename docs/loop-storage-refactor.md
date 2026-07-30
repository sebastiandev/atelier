# Spec: loop / stage / manifest storage refactor

Status: draft, pending sign-off. One commit once approved.

## Why

Four problems, one shape change.

1. **`.atelier/` is a guest marker used in Atelier's own house.** It exists so
   Atelier can keep state inside *someone else's* repo without polluting it
   (`<repo>/.atelier/planning/<WRK>/`). But the same suffix is appended to
   Atelier's own workspace — `~/Atelier/.atelier/loops/` — a hidden folder
   inside the folder that already *is* Atelier. And in a real user repo the
   folder is untracked and un-gitignored (`git ls-files .atelier` empty,
   `check-ignore` exits 1), so the "commit planning to GitHub" rationale it was
   built for never happened.

2. **The loop/stage format can't be shared.** `loop.yaml` references
   `steps/<id>.md` for every instruction, so a single-file share is broken by
   construction — you cannot hand someone one file that is a whole loop. This
   directly blocks the import feature that motivates the whole line of work.

3. **`REPOSITORY` scope is unexercised.** No repo on disk has ever held
   `.atelier/loops/`. It is an intent, not a feature.

4. **The planning pointer is redundant.** `works/<W>/planning.json` exists only
   to *locate* the manifest in an arbitrary repo. `root_path` is already a key
   *inside* the manifest, so the pointer stores the same value a second time to
   find the first. The current state proves the fragility: 4 manifests exist but
   only 2 pointers, so two works are unreachable.

## Target on-disk layout

| what | before | after |
|---|---|---|
| loop library | `~/Atelier/.atelier/loops/<id>/` | `~/Atelier/loops/<id>/` |
| stage library | `~/Atelier/.atelier/stages/<id>/` | `~/Atelier/stages/<id>/` |
| work-local loops | `~/Atelier/works/<W>/.atelier/loops/<id>/` | `~/Atelier/works/<W>/loops/<id>/` |
| planning manifest | `<selected>/.atelier/planning/<W>/manifest.json` | `~/Atelier/works/<W>/planning/manifest.json` |
| planning pointer | `~/Atelier/works/<W>/planning.json` | *deleted* |
| framework docs | `<selected>/…` | unchanged (user's repo, committed) |

`loops/` and `stages/` are **siblings**, not nested. The domain says a stage is
defined without a loop (the stage editor says so in those words), and
`loops/stages/` would collide with a loop whose id is `stages`.

The `.atelier/` directory disappears entirely — from the Atelier workspace and
from user repos alike. Loops and stages only ever lived under `~/Atelier`
(library and work scopes; `REPOSITORY` is being dropped), and the manifest was
the only `.atelier/` resident in a user's selected folder — it moves to the
work dir. Framework docs stay where the user put them, but those were never
under `.atelier/` to begin with.

## Scope change

`LoopDefinitionScope.REPOSITORY` is no longer produced. The enum *value* stays
readable because historical `definition_snapshot` rows in SQL contain it; the
serializer and catalog stop emitting it. Catalog overlay collapses from four
tiers to three: work > library > builtin (legacy read-fallback retained for the
migration window only).

## Manifest relocation

The manifest moves to a **known path** under the work dir, so nothing needs to
locate it. `root_path` inside the manifest remains the single record of where
the framework docs live. Consequences:

- `planning.json` and all pointer read/write code is deleted.
- `FsPlanningFiles` resolves the manifest at `works/<W>/planning/manifest.json`
  directly instead of pointer → root → `.atelier/planning/<W>`.
- `actions.py` docstring "the manifest lives in the user's repository" becomes
  false and is corrected. `artifact_runs` (ids only; SQL is canonical) is
  unaffected.
- Framework docs stay where the user put them; only Atelier-internal state moves
  home.

## YAML schema v2

Bump `schema_version` to `2`. The shape changes in four ways.

### Inline instructions

Today `instructions` is **always** a path, on read and write, whenever
non-empty (`deterministic_check` stages inline `''` only because they have no
prompt). v2 makes it an inline block scalar:

```yaml
instructions: |
  Implement the target artifact end to end.
  Follow the acceptance criteria exactly; do not expand scope.
```

Rationale: import needs one file; `steps/*.md` leaks (the writer emits a file
per current stage but nothing prunes when a stage is removed — only whole-
definition delete does `rmtree`); prompts are small (7-9 lines, ~400 chars).
The `steps/` directory is removed.

### Unified stage reference

Today an inline stage and a referenced stage are two disjoint schemas in one
list, distinguished by probing for `from`. v2 uses one discriminator, `use`:

```yaml
stages:
  - id: implementation            # inline: no `use`
    name: Implementation
    kind: agent_task
    instructions: |
      …
    transitions:
      pass: shiphero-code-checks

  - id: shiphero-code-checks       # reference: has `use`
    use: shiphero-code-checks@36d6f6
    overrides:
      agent: { effort: low }
    transitions:
      pass: code-review
```

`use: <id>@<rev>` folds `from` + `from_rev` into one pinned token. `overrides`
replaces `overrides` (unchanged name — it matches the domain type
`StageOverrides`; `with:` was rejected because it means *inputs* elsewhere, not
replacement). Presence of `use` is the only discriminator.

### `steps:` → `stages:`

The top-level list key becomes `stages`, matching `StageDefinition`,
`stage_ref`, the `stages/` folder, and the UI's Stages tab.

### Omit defaults

The serializer stops emitting fields at their default (`agent: null`,
`transitions: {}`, `instructions: ''`, `step: null`, `ref: null`). A shared or
hand-authored file shows only what is set.

## Versioning strategy

**One shape. `schema_version` stays `1`, redefined to the new shape. Everything
is migrated to it. No dual-read parse paths in the runtime.**

Single-user, so dual-shape read code is permanent complexity paid for a
transition a single migration run finishes. We decline it. The old read logic
lives only in the migration script — the app knows one shape.

The one hazard is the window between deploying this code (`--reload` picks it up
immediately) and running the FS migration: new reader, not-yet-migrated files.
An old `loop.yaml` still says `schema_version: 1`, so the number cannot
distinguish it — but the `steps:` → `stages:` key rename does, unambiguously.
The reader therefore *rejects* an old-shape file with an actionable
`LoopSchemaOutdated: pre-refactor loop file; run scripts/migrate-loops.py`
rather than misparsing `instructions: steps/x.md` as literal instruction text.
This is refusal, not a second parse path. For stage files the discriminator is
an `instructions` value ending in `.md` (a path where the new shape expects
inline text or nothing).

The migration is run immediately after the commit (with a backup), so the
window is closed by hand rather than left to chance. The `legacy` root fallback
is retained briefly, but it is a *location* fallback (read the old directory)
not a *format* fallback — the two must not be conflated.

## Location abstraction

`.atelier/loops` is currently hardcoded in four places
(`loop_definitions.py:471`, `stage_definitions.py:182`, `reveal_definition.py:66`,
`commands/stages.py:169`), which is how the suffix ended up wrong for two of the
three roots. The `LoopDefinitionLocations` port changes from returning a *root*
the repository decorates to returning the *directory* itself
(`loops_dir()` / `stages_dir()`). Four append sites collapse to one, and the
Atelier-workspace vs user-repo layout difference lives in one function.

## Migration (`scripts/migrate-loops.py`)

One idempotent, atomic-per-file script, on the `migrate-plan-manifests.py`
pattern. `/migrate` auto-discovers it. Steps:

1. **Move directories.** `~/Atelier/.atelier/loops/*` → `~/Atelier/loops/`,
   `~/Atelier/.atelier/stages/*` → `~/Atelier/stages/`, and each
   `works/<W>/.atelier/loops/*` → `works/<W>/loops/`.
2. **Rewrite each `loop.yaml` / `stage.yaml` v1 → v2:** inline the `steps/*.md`
   bodies, `steps:` → `stages:`, `from`/`from_rev` → `use:`, drop defaults, set
   `schema_version: 2`, delete the `steps/` dir.
3. **Move manifests.** For every work, `<root>/.atelier/planning/<W>/manifest.json`
   (root from the old pointer, or the Atelier workspace when no pointer)
   → `~/Atelier/works/<W>/planning/manifest.json`. Delete the pointer.
4. **Leave framework docs untouched.**

Idempotent: a file already at v2 in the new location is reported `current`.
Atomic: `.tmp` then `replace`, so a failure leaves the original intact.

## Why active works don't break

- **In-flight runs never read the file.** `monitor.py:207` and `:687` resolve
  the definition from `definition_from_snapshot(loop["definition_snapshot"])` —
  SQL, not `loop.yaml`. WRK-002 and WRK-014 can be migrated (or their loop
  files deleted) mid-run without effect.
- **The manifest move is ordered.** Migration runs before the app reads the new
  location; `FsPlanningFiles` is repointed in the same commit. The manifest's
  own `root_path` survives the move, so framework-doc resolution is unchanged.
- **`legacy` location fallback** means a directory the script misses degrades to
  "loop not found in library" rather than data loss, and re-running the script
  picks it up.
- **DB schema is untouched** by this change (no new migration; v25 stays).

## Test plan

- Unit: v2 serializer omits defaults; `use: id@rev` round-trips to a
  `stage_ref`; inline instructions round-trip; a v1-shaped dict is rejected by
  the runtime reader with the actionable message.
- Unit: `loops_dir()` / `stages_dir()` return the bare path under the Atelier
  workspace and the `.atelier`-suffixed path under a user repo.
- Integration: catalog lists library + work loops from the new locations; a
  work-local loop still overlays a library loop of the same id.
- Migration: a fixture tree with a v1 loop (with `steps/*.md`), a v1 stage, a
  manifest behind a pointer, and a manifest with no pointer — all land at v2 in
  the new layout, idempotent on a second run, framework docs untouched.
- Full backend suite + ruff + mypy + tsc + vite build.

## Out of scope

- **Import.** Separate feature, discussed after this lands. This refactor only
  makes the format importable (one self-contained file); it adds no import path,
  no id-collision policy, no trust surfacing.
- **Builtins as files.** They stay in Python. Reveal stays honestly disabled for
  a builtin, because there genuinely is no file until the user forks it.
- **`artifact_runs` shape.** The manifest still records run ids; whether SQL
  should be the sole record is a separate question.

## Resolved decisions

- Drop `REPOSITORY`. · `.atelier/` gone from the Atelier workspace. ·
  `loops/` + `stages/` siblings. · Manifest to `works/<W>/planning/`, pointer
  deleted. · Builtins stay code. · Inline instructions. · `use:` + `overrides:`.
  · `stages:` key. · Omit defaults. · Bump to schema_version 2, runtime reads v2
  only, eager one-shot migration, no dual-read. · One commit.
