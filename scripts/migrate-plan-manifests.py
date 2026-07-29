#!/usr/bin/env python3
"""One-shot rename of the plan-artifacts keys in planning manifests.

``artifact_root`` held the plan-artifacts folder relative to the work root
and ``artifact_root_path`` its absolute resolution. Both are now
``plan_artifacts_dir`` and ``plan_artifacts_path`` — "artifact" already
means the PR/Jira entity elsewhere, and the old pair read as one name with
a suffix rather than two different things.

Reads accept either spelling, so an unmigrated work keeps opening and
converges the next time anything writes its manifest. This converts them
eagerly instead, which matters for a work whose manifest is not otherwise
touched for a while.

Searches ``~/Atelier/.atelier/planning/*/manifest.json`` (override the root
with ``ATELIER_WORKSPACE_ROOT``) plus every repository named by a
``works/*/planning.json`` pointer, because a plan materialised into a source
repo keeps its manifest there rather than under the Atelier root. Pass extra
repository paths as arguments to cover repos no pointer names.

Idempotent, and atomic per file (``.tmp`` then ``replace``) so a failure
leaves the original intact.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RENAMES = (
    ("artifact_root", "plan_artifacts_dir"),
    ("artifact_root_path", "plan_artifacts_path"),
)


def main(argv: list[str]) -> int:
    root = Path(os.environ.get("ATELIER_WORKSPACE_ROOT", str(Path.home() / "Atelier")))
    manifests = sorted(
        {
            *root.glob(".atelier/planning/*/manifest.json"),
            *(
                path
                for repo in _repo_roots(root, argv)
                for path in repo.glob(".atelier/planning/*/manifest.json")
            ),
        }
    )
    if not manifests:
        print(f"no planning manifests found under {root} or any Work's repository")
        return 0
    changed = 0
    for path in manifests:
        state = rewrite(path)
        print(f"  {state:9} {path}")
        if state == "migrated":
            changed += 1
    print(f"done · {changed}/{len(manifests)} manifests updated")
    return 0


def _repo_roots(root: Path, argv: list[str]) -> list[Path]:
    """Repositories a Work points at, plus any passed on the command line."""
    roots: list[Path] = [Path(arg).expanduser() for arg in argv]
    # `works/<slug>/planning.json` is the planning pointer: it names the
    # repository a Work's plan was materialised into, which is where that
    # Work's manifest lives when it is not under the Atelier root.
    for pointer in root.glob("works/*/planning.json"):
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        value = data.get("root_path") if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            roots.append(Path(value).expanduser())
    return [path for path in dict.fromkeys(roots) if path.is_dir()]


def rewrite(path: Path) -> str:
    """Return 'migrated', 'current', or 'skipped'."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ! cannot read {path}: {exc}", file=sys.stderr)
        return "skipped"
    if not isinstance(manifest, dict):
        return "skipped"
    modified = False
    for legacy, current in RENAMES:
        if legacy not in manifest:
            continue
        # Never clobber a populated new key with a stale legacy one.
        if not manifest.get(current):
            manifest[current] = manifest[legacy]
        del manifest[legacy]
        modified = True
    if not modified:
        return "current"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return "migrated"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
