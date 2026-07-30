#!/usr/bin/env python3
"""One-off: rename specific loop/stage ids everywhere they are referenced.

Not a `/migrate` script — this fixes two ids that only exist on this
install, artefacts of the old fork naming (`<source-id>-repository` /
`-library`) that drifted from their edited display names. The slugify
convention now mints ids from the name at creation, so this is a
back-fill, not a recurring migration.

A loop/stage id is referenced in more than one place, so all of them move
together:

- the library directory and (for a loop) every work overlay of the same id
- the `id:` field inside each YAML, and any `forked_from:` pointing at it
- for a loop: `loop_runs.definition_id`, and the id embedded in the frozen
  `definition_snapshot` / `state` JSON of every run (renaming a label in
  history — the run behaviour is identical)

Dry-run by default; pass `--apply` to write. Back up first; this mutates
the live database.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("ATELIER_WORKSPACE_ROOT", str(Path.home() / "Atelier")))
DB = WORKSPACE / "atelier.db"

# (kind, old_id, new_id)
RENAMES = [
    ("loop", "atelier-reviewed-library", "shiphero-code-review"),
    ("stage", "code-review-repository", "shiphero-code-review"),
]


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    for kind, old, new in RENAMES:
        print(f"== {kind}: {old} -> {new}")
        if kind == "loop":
            _rename_loop_fs(old, new, apply)
            _rename_loop_db(old, new, apply)
        else:
            _rename_stage_fs(old, new, apply)
    print("applied." if apply else "dry run — pass --apply to write.")
    return 0


def _loop_dirs(old: str) -> list[Path]:
    return [
        p
        for p in [WORKSPACE / "loops" / old, *WORKSPACE.glob(f"works/*/loops/{old}")]
        if p.is_dir()
    ]


def _rename_loop_fs(old: str, new: str, apply: bool) -> None:
    for directory in _loop_dirs(old):
        dest = directory.with_name(new)
        yaml_path = directory / "loop.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        text = text.replace(f"id: {old}\n", f"id: {new}\n")
        text = text.replace(f"forked_from: {old}\n", f"forked_from: {new}\n")
        print(f"   fs: {directory} -> {dest}")
        if apply:
            yaml_path.write_text(text, encoding="utf-8")
            directory.rename(dest)


def _rename_stage_fs(old: str, new: str, apply: bool) -> None:
    directory = WORKSPACE / "stages" / old
    if not directory.is_dir():
        print(f"   fs: {directory} (absent, skipped)")
        return
    dest = directory.with_name(new)
    yaml_path = directory / "stage.yaml"
    text = yaml_path.read_text(encoding="utf-8").replace(f"id: {old}\n", f"id: {new}\n")
    print(f"   fs: {directory} -> {dest}")
    if apply:
        yaml_path.write_text(text, encoding="utf-8")
        directory.rename(dest)


def _rename_loop_db(old: str, new: str, apply: bool) -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, definition_id, definition_snapshot, state "
            "FROM loop_runs WHERE definition_id = ? OR definition_snapshot LIKE ?",
            (old, f"%{old}%"),
        ).fetchall()
        print(f"   db: {len(rows)} loop_runs")
        if not apply:
            return
        for row in rows:
            snapshot = _replace_id(json.loads(row["definition_snapshot"]), old, new)
            state = _replace_id(json.loads(row["state"]), old, new)
            conn.execute(
                "UPDATE loop_runs SET definition_id = ?, definition_snapshot = ?, "
                "state = ? WHERE id = ?",
                (new, json.dumps(snapshot), json.dumps(state), row["id"]),
            )
        conn.commit()
    finally:
        conn.close()


def _replace_id(obj: object, old: str, new: str) -> object:
    """Replace any string value equal to ``old`` — never a substring, so a
    ``run_key`` or unrelated field can't be caught."""
    if isinstance(obj, dict):
        return {k: _replace_id(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_id(v, old, new) for v in obj]
    if obj == old:
        return new
    return obj


def _backup() -> None:
    shutil.copy2(DB, DB.with_suffix(".db.bak-rename-loop-ids"))
    for name in ("loops", "stages"):
        src = WORKSPACE / name
        if src.is_dir():
            shutil.copytree(src, WORKSPACE / f"{name}.bak-rename-loop-ids", dirs_exist_ok=True)
    print(f"backed up {DB} and loops/ stages/ (*.bak-rename-loop-ids)")


if __name__ == "__main__":
    if "--apply" in sys.argv:
        _backup()
    raise SystemExit(main(sys.argv[1:]))
