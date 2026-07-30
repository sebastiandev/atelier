#!/usr/bin/env python3
"""One-shot migration for the loop / stage / manifest storage refactor.

Three moves, all on data under ``~/Atelier`` (override the root with
``ATELIER_WORKSPACE_ROOT``) plus repos named by ``works/*/planning.json``:

1. **Layout.** ``<root>/.atelier/loops`` -> ``<root>/loops`` and
   ``.atelier/stages`` -> ``stages``, for the Atelier workspace and each
   ``works/<W>``. The ``.atelier`` marker was only ever right inside a
   *user's* repo; Atelier's own tree should not hide its library.

2. **Schema.** Each ``loop.yaml`` / ``stage.yaml`` is rewritten to the new
   shape: instructions inline (``steps/*.md`` folded in, the folder removed),
   ``steps:`` -> ``stages:``, ``from`` + ``from_rev`` -> ``use: id@rev``.

3. **Manifests.** ``<repo>/.atelier/planning/<W>/manifest.json`` ->
   ``~/Atelier/works/<W>/planning/manifest.json`` (a known Atelier path, so
   no pointer is needed to find it), applying the ``artifact_root`` ->
   ``plan_artifacts_*`` key rename in the same pass. The ``planning.json``
   pointer is deleted.

Idempotent and atomic per file (``.tmp`` then ``replace``); a file already
in the new shape/location is reported ``current``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

MANIFEST_KEY_RENAMES = (
    ("artifact_root", "plan_artifacts_dir"),
    ("artifact_root_path", "plan_artifacts_path"),
)


def main(argv: list[str]) -> int:
    root = Path(os.environ.get("ATELIER_WORKSPACE_ROOT", str(Path.home() / "Atelier")))
    changed = 0
    changed += _migrate_libraries(root, argv)
    changed += _migrate_manifests(root, argv)
    print(f"done · {changed} paths updated")
    return 0


# --- 1 + 2: loop / stage libraries -----------------------------------------


def _migrate_libraries(root: Path, argv: list[str]) -> int:
    bases = [root, *(root.glob("works/*")), *_repo_roots(root, argv)]
    changed = 0
    for base in dict.fromkeys(bases):
        for kind in ("loops", "stages"):
            old = base / ".atelier" / kind
            new = base / kind
            if old.is_dir():
                changed += _move_library(old, new)
        # Rewrite whatever now lives at the new location, old shape or not.
        for kind, rewrite in (("loops", _rewrite_loop), ("stages", _rewrite_stage)):
            target = base / kind
            if not target.is_dir():
                continue
            for definition_dir in sorted(p for p in target.iterdir() if p.is_dir()):
                state = rewrite(definition_dir)
                if state:
                    print(f"  {state:9} {definition_dir}")
                    if state == "migrated":
                        changed += 1
    return changed


def _move_library(old: Path, new: Path) -> int:
    new.mkdir(parents=True, exist_ok=True)
    moved = 0
    for entry in old.iterdir():
        dest = new / entry.name
        if dest.exists():
            continue  # already moved on a prior run
        shutil.move(str(entry), str(dest))
        moved += 1
    if not any(old.iterdir()):
        old.rmdir()
        _prune_empty_atelier(old.parent)
    if moved:
        print(f"  moved     {old} -> {new} ({moved})")
    return moved


def _prune_empty_atelier(atelier_dir: Path) -> None:
    if atelier_dir.name == ".atelier" and atelier_dir.is_dir() and not any(
        atelier_dir.iterdir()
    ):
        atelier_dir.rmdir()


def _rewrite_loop(directory: Path) -> str:
    path = directory / "loop.yaml"
    raw = _load_yaml(path)
    if raw is None:
        return ""
    if "steps" not in raw:
        return "current"
    steps = raw.pop("steps")
    raw["stages"] = [_rewrite_loop_stage(directory, item) for item in steps]
    _write_yaml(path, raw)
    shutil.rmtree(directory / "steps", ignore_errors=True)
    return "migrated"


def _rewrite_loop_stage(directory: Path, stage: dict) -> dict:
    if "from" in stage:
        pinned = stage.pop("from_rev", None)
        source = stage.pop("from")
        out: dict = {"id": stage["id"], "use": f"{source}@{pinned}" if pinned else source}
        if "overrides" in stage:
            out["overrides"] = stage["overrides"]
        if stage.get("transitions"):
            out["transitions"] = stage["transitions"]
        return out
    _inline_instructions(directory, stage)
    return stage


def _rewrite_stage(directory: Path) -> str:
    path = directory / "stage.yaml"
    raw = _load_yaml(path)
    if raw is None:
        return ""
    stage = raw.get("stage")
    if not isinstance(stage, dict):
        return ""
    instructions = stage.get("instructions")
    is_path = isinstance(instructions, str) and instructions.rstrip().endswith(".md")
    if not is_path:
        return "current"
    _inline_instructions(directory, stage)
    _write_yaml(path, raw)
    shutil.rmtree(directory / "steps", ignore_errors=True)
    return "migrated"


def _inline_instructions(directory: Path, stage: dict) -> None:
    ref = stage.get("instructions")
    if isinstance(ref, str) and ref.rstrip().endswith(".md"):
        body = (directory / ref).read_text(encoding="utf-8") if (directory / ref).is_file() else ""
        text = body.strip()
        if text:
            stage["instructions"] = text + "\n"
        else:
            stage.pop("instructions", None)


# --- 3: manifests -----------------------------------------------------------


def _migrate_manifests(root: Path, argv: list[str]) -> int:
    changed = 0
    seen: set[str] = set()
    # Pointer-directed moves (a plan materialised into a user repo).
    for pointer in sorted(root.glob("works/*/planning.json")):
        work_slug = pointer.parent.name
        seen.add(work_slug)
        source = _old_manifest_path(root, pointer, work_slug, argv)
        changed += _relocate_manifest(root, work_slug, source)
        pointer.unlink(missing_ok=True)
    # Atelier-workspace-rooted plans have no pointer; sweep the old location.
    for old in sorted(root.glob(".atelier/planning/*/manifest.json")):
        work_slug = old.parent.name
        if work_slug in seen:
            continue
        changed += _relocate_manifest(root, work_slug, old)
    return changed


def _relocate_manifest(root: Path, work_slug: str, source: Path | None) -> int:
    """Move one manifest to the known Atelier path; a true move, not a copy.

    Only ``manifest.json`` is taken — a no-repo plan keeps its framework docs
    beside it, so the containing ``.atelier/planning/<W>`` is pruned only when
    nothing else remains.
    """
    dest = root / "works" / work_slug / "planning" / "manifest.json"
    did_work = False
    if not dest.exists():
        if source is None or not source.is_file():
            if source is not None:
                print(f"  ! no manifest for {work_slug} at {source}", file=sys.stderr)
            return 0
        manifest = json.loads(source.read_text(encoding="utf-8"))
        for legacy, current in MANIFEST_KEY_RENAMES:
            if legacy in manifest:
                if not manifest.get(current):
                    manifest[current] = manifest[legacy]
                del manifest[legacy]
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        tmp.replace(dest)
        print(f"  moved     {source} -> {dest}")
        did_work = True
    # Remove the stale source and prune empty planning dirs, whether the move
    # happened now or on a prior run (so re-running cleans a half-done state).
    if source is not None and source != dest and source.is_file():
        source.unlink()
        _prune_empty(source.parent)
        did_work = True
    return 1 if did_work else 0


def _prune_empty(directory: Path) -> None:
    """Remove now-empty ``.atelier/planning/<W>`` and its parents up to
    ``.atelier``; stop at the first non-empty (a no-repo plan's docs)."""
    for candidate in (directory, directory.parent, directory.parent.parent):
        if (
            candidate.name in {directory.name, "planning", ".atelier"}
            and candidate.is_dir()
            and not any(candidate.iterdir())
        ):
            candidate.rmdir()


def _old_manifest_path(
    root: Path, pointer: Path, work_slug: str, argv: list[str]
) -> Path | None:
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    root_path = data.get("root_path") if isinstance(data, dict) else None
    candidates = []
    if isinstance(root_path, str) and root_path.strip():
        candidates.append(Path(root_path).expanduser())
    candidates.append(root)  # Atelier-workspace-rooted plan
    for base in candidates:
        candidate = base / ".atelier" / "planning" / work_slug / "manifest.json"
        if candidate.is_file():
            return candidate
    return None


def _repo_roots(root: Path, argv: list[str]) -> list[Path]:
    roots = [Path(arg).expanduser() for arg in argv]
    for pointer in root.glob("works/*/planning.json"):
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        value = data.get("root_path") if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            roots.append(Path(value).expanduser())
    return [p for p in dict.fromkeys(roots) if p.is_dir()]


def _load_yaml(path: Path) -> dict | None:
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None


def _write_yaml(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")
    tmp.replace(path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
