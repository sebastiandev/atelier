# Spec: loop / stage import & export

Status: draft, pending sign-off. Written after the storage refactor made a
loop a single self-contained file.

## Why

A loop is now one `loop.yaml` with inline instructions, so it can be
shared — except when it references library stages by `use: <id>@<rev>`,
which the recipient may not have. Export resolves that by flattening the
references into inline stages while recording that they *were* links, so
import can rebuild them against the recipient's own library. The result:
a file that works anywhere, and re-linking when the recipient wants it.

There is no export today, so this is export **and** import.

## Export

Export serialises the *resolved* loop — the shape the reader already
produces, where every `use:` reference is materialised inline by
`resolve_stage_link` and still carries its `stage_ref` (id + revision).

- **Genuinely-inline stages** serialise as they are stored.
- **Linked stages** serialise with their full inline body **plus**
  `linked_from: <stage-id>@<rev>` (from `stage_ref`). The body makes the
  file work even if never re-linked; `linked_from` is the provenance that
  lets import rebuild the link.

```yaml
schema_version: 1
id: shiphero-code
name: Shiphero-code
stages:
  - id: implementation          # genuinely inline
    kind: agent_task
    instructions: |
      …
  - id: shiphero-code-checks     # was a library link
    linked_from: shiphero-code-checks@36d6f6
    kind: deterministic_check
    check: { adapter: command, command: [uv, run, dt, sh] }
    transitions: { pass: code-review }
```

`linked_from` is **transport-only**: it exists in an exported file, is
consumed at import, and is never written to storage. The on-disk
`loop.yaml` format is unchanged — after import a linked stage is a real
`use:` reference again (or genuinely inline, if the importer chose). Only
the export serialiser emits `linked_from`; only the import parser reads it.

Export is always a flatten; there is no "keep links" option until asked.

Stages export too, as a standalone `stage.yaml` (no references, so nothing
to flatten).

## Transport

**File upload only.** Paste-YAML is error-prone and omitted. Export is a
file download; import takes an uploaded file.

## Import

Import is a create through the id convention we already have: the id is
minted from the name, and a name that slugs to a taken id is a conflict.
The wrinkle is the linked stages, which need per-stage decisions — so
import is two calls, a preview then a commit.

### Parse

The import parser is distinct from the normal reader: it must *not*
resolve `use:`/`linked_from` against the local library (the stages may not
exist yet). It produces the loop's metadata plus a list of stages, each
either genuinely-inline or linked (inline body + `linked_from: id@rev`).

### Reconstitution — the per-linked-stage decision

For each stage carrying `linked_from: id@rev`:

| local state | action |
|---|---|
| no local stage with `id` | create it in the library from the inline body; rewrite the loop stage to `use: id@<new-rev>` |
| local `id` exists, **same rev** | identical content; link to the local one, no prompt |
| local `id` exists, **different rev** | conflict → the importer chooses (below) |

A different-revision conflict offers three resolutions:

- **Replace** the local stage with the imported one. Bumps its revision, so
  every *other* loop that links it picks up the change on its next run —
  the preview **warns with the count of other loops that use it**.
- **Use existing** — keep the local stage, link the loop to it, discard the
  imported body.
- **New id** — create the imported stage under a fresh id (slug + numeric
  suffix, or a name the importer gives), and point *this loop's* `use:` at
  the new id. Both stages survive.

Because ids are minted from names, two people's "Code review" both slug to
`code-review`; a collision is often two *different* stages, which is why
the revision check and the three-way choice exist rather than a blind
overwrite.

Genuinely-inline stages (no `linked_from`) stay inline; nothing to decide.

### Safety surface — surface and accept

An imported loop carries executable content from a stranger. The preview
lists, grouped by stage:

- every `approved_command_prefixes` entry (auto-allowed shell commands — the
  sharp edge)
- every `agent.permissions: write`

The importer accepts them explicitly in the commit call. Nothing is
silently granted; nothing is stripped without the importer seeing it.

### Atomicity

All-or-nothing. If any reconstituted stage fails to create, the whole
import rolls back — a half-imported loop is invalid anyway. The loop and
all its new/replaced stages commit together or not at all.

### Endpoints

1. `POST /api/loops/import/preview` (file upload) → parse + validate, return:
   - the derived loop id and whether it collides
   - per stage: `inline` / `link-clean` / `link-conflict` (with the local
     revision and the count of other loops using it)
   - the safety surface (command prefixes + write permissions, per stage)
   - validation errors, if any (invalid loops are not importable)
2. `POST /api/loops/import` → the file plus resolutions:
   - accepted command prefixes
   - per conflict stage: replace / use-existing / new-id (+ the new id)
   - creates the loop (mint-from-name) and any stages, atomically

Stage import mirrors this with a single collision decision and no
reconstitution: `POST /api/stages/import/preview` and `/api/stages/import`.

## Frontend

An import review dialog: upload → preview → resolve. It shows the loop's
name/id (and any collision), a row per stage with its
inline/link-clean/link-conflict status and the resolution control for
conflicts, the blast-radius warning on Replace, and the safety surface with
an explicit accept. Confirm calls the commit endpoint. Export is a download
button on a loop/stage.

## Architecture

- **domain** — the flatten serialiser (pure, over a resolved
  `LoopDefinition`), the import parser (transport YAML → an `ImportedLoop`
  model that keeps linked stages unresolved), and the reconstitution +
  preview logic (pure functions over the local catalog).
- **application** — the four endpoints, file-upload handling, mapping the
  preview/commit models to the wire.
- **infrastructure** — YAML read/write of the transport shape; stage/loop
  creation reuses the existing repositories and the mint-from-name commands.

## Test plan

- Export: a loop with one inline and one linked stage round-trips to a file
  with `linked_from` on the linked one only; the file re-imports to the same
  loop. A loop with no links exports with no `linked_from`.
- Import reconstitution: each row of the decision table — fresh create,
  same-rev silent link, and all three different-rev resolutions — asserting
  the resulting loop's `use:` targets and the library's stages.
- Collision on the loop id → 409 / rename.
- Safety surface: command prefixes and write permissions appear in the
  preview and are not granted without acceptance.
- Rollback: a forced failure on the second of two stage creations leaves no
  loop and no stage behind.
- Blast-radius: Replace on a stage used by N loops reports N.
- Stage-only import: create, and each collision resolution.
- Full suite + ruff + mypy + tsc + build.

## Out of scope

- URL / gist import (a network + trust escalation).
- Paste-YAML transport.
- Re-linking an already-imported inline stage after the fact (import is the
  only re-link point for now).
- Signing / provenance beyond `linked_from`.

## Resolved decisions

- Flatten on export, mark linked stages `linked_from: <id>@<rev>`
  (transport-only). · Surface-and-accept command prefixes. · Upload only. ·
  Loops and stages. · Reconstitute linked stages on import with the
  same-rev/different-rev decision table. · Replace warns with the count of
  other loops using the stage. · Import is atomic (rollback). · Import id
  minted from the name, like any create.

## Implementation notes (as built)

- **Revision comparability holds.** `definition_revision` /
  `stage_definition_revision` are pure SHA-256 hashes over
  `dataclasses.asdict` → canonical JSON, with no machine-local data
  (`backend/src/domain/loop/definitions.py:117`,
  `backend/src/domain/loop/stages.py:104`). So "same rev → silent link" is
  exact. The hash also covers `name`/`description`/`scope`/`forked_from`, so
  it only ever errs toward *conflict* (never a false silent link) — the safe
  direction, no fallback needed.
- **Transport keys.** Each stage keeps the resolved snapshot shape
  (`loop_stage_snapshot`) so import can reuse `loop_stage_from_snapshot`
  verbatim; `stage_ref`/`overrides` are dropped. A linked stage adds
  `linked_from: <id>@<rev>`, plus `outcomes` and (when set) `description` /
  `forked_from` from the source stage — the extra provenance needed to
  reconstitute a faithful, revision-comparable `StageDefinition`. Domain
  serialiser/parser: `backend/src/domain/loop/transport.py`.
- **Wire is JSON, not multipart.** "Upload only" is honoured as a product
  stance (a file picker, no paste box); the browser reads the file text and
  posts it in a JSON body (`content`), matching every existing route and
  letting preview→commit reuse the same text. `python-multipart` remains
  available if a true `UploadFile` is ever wanted.
- **Layering.** Pure serialise/parse/classify in `domain/loop/transport.py`;
  YAML codec in `infrastructure/filesystem/loop_transport.py`; use cases in
  `domain/commands/loops/export_definition.py`,
  `domain/commands/loops/import_definition.py` (preview + atomic commit with
  compensating rollback), and `domain/commands/stages_import.py`. Endpoints:
  `GET /api/loops/{id}/export`, `POST /api/loops/import/preview`,
  `POST /api/loops/import` (and the `/api/stages/...` mirror).
