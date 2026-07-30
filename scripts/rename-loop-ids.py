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

# (kind, old_id, new_id, old_name, new_name). A name is renamed too when
# given; ``None`` leaves it. Entries are idempotent — an old id that no
# longer exists is a no-op — so this file is a running record of every id
# fix applied to this install.
RENAMES = [
    # 1. the fork-suffix artefacts, back-filled to slugs of their names.
    ("loop", "atelier-reviewed-library", "shiphero-code-review", None, None),
    ("stage", "code-review-repository", "shiphero-code-review", None, None),
    # 2. shorten the loop's name and id to match.
    (
        "loop",
        "shiphero-code-review",
        "shiphero-code",
        "ShipHero Code & Review",
        "Shiphero-code",
    ),
]


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    for kind, old, new, old_name, new_name in RENAMES:
        print(f"== {kind}: {old} -> {new}" + (f" (name -> {new_name!r})" if new_name else ""))
        if kind == "loop":
            _rename_loop_fs(old, new, old_name, new_name, apply)
            _rename_loop_db(old, new, old_name, new_name, apply)
        else:
            _rename_stage_fs(old, new, old_name, new_name, apply)
    print("applied." if apply else "dry run — pass --apply to write.")
    return 0


def _loop_dirs(old: str) -> list[Path]:
    return [
        p
        for p in [WORKSPACE / "loops" / old, *WORKSPACE.glob(f"works/*/loops/{old}")]
        if p.is_dir()
    ]


def _rewrite_yaml(text: str, old: str, new: str, old_name: str | None, new_name: str | None) -> str:
    text = text.replace(f"id: {old}\n", f"id: {new}\n")
    text = text.replace(f"forked_from: {old}\n", f"forked_from: {new}\n")
    if old_name and new_name:
        text = text.replace(f"name: {old_name}\n", f"name: {new_name}\n")
    return text


def _rename_loop_fs(
    old: str, new: str, old_name: str | None, new_name: str | None, apply: bool
) -> None:
    for directory in _loop_dirs(old):
        dest = directory.with_name(new)
        yaml_path = directory / "loop.yaml"
        text = _rewrite_yaml(yaml_path.read_text(encoding="utf-8"), old, new, old_name, new_name)
        print(f"   fs: {directory} -> {dest}")
        if apply:
            yaml_path.write_text(text, encoding="utf-8")
            directory.rename(dest)


def _rename_stage_fs(
    old: str, new: str, old_name: str | None, new_name: str | None, apply: bool
) -> None:
    directory = WORKSPACE / "stages" / old
    if not directory.is_dir():
        print(f"   fs: {directory} (absent, skipped)")
        return
    dest = directory.with_name(new)
    yaml_path = directory / "stage.yaml"
    text = _rewrite_yaml(yaml_path.read_text(encoding="utf-8"), old, new, old_name, new_name)
    print(f"   fs: {directory} -> {dest}")
    if apply:
        yaml_path.write_text(text, encoding="utf-8")
        directory.rename(dest)


def _rename_loop_db(
    old: str, new: str, old_name: str | None, new_name: str | None, apply: bool
) -> None:
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
        pairs = [(old, new)]
        if old_name and new_name:
            pairs.append((old_name, new_name))
        for row in rows:
            snapshot = _replace_values(json.loads(row["definition_snapshot"]), pairs)
            state = _replace_values(json.loads(row["state"]), pairs)
            conn.execute(
                "UPDATE loop_runs SET definition_id = ?, definition_snapshot = ?, "
                "state = ? WHERE id = ?",
                (new, json.dumps(snapshot), json.dumps(state), row["id"]),
            )
        conn.commit()
    finally:
        conn.close()


def _replace_values(obj: object, pairs: list[tuple[str, str]]) -> object:
    """Replace any string value equal to an ``old`` — whole-value only, so a
    ``run_key`` or a substring can't be caught."""
    if isinstance(obj, dict):
        return {k: _replace_values(v, pairs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_values(v, pairs) for v in obj]
    if isinstance(obj, str):
        for old, new in pairs:
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
