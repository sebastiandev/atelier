#!/usr/bin/env python3
"""One-shot rewrite of transcript.ndjson files into Atelier's canonical
tool shape.

Run after upgrading to the canonical-tool-shape change. Walks every
``~/Atelier/works/*/agents/*/transcript.ndjson`` (override with
``ATELIER_WORKSPACE_ROOT``) and rewrites ``tool_call`` /
``permission_request`` events through ``canonicalize_tool``. Idempotent —
already-canonical events pass through unchanged.

Each file is written atomically (``.tmp`` then ``replace``); failures
leave the original intact.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Allow `python scripts/migrate-transcripts.py` from repo root without
# requiring `pip install -e backend/`.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from src.infrastructure.agents.tool_canonical import canonicalize_tool  # noqa: E402


def main() -> int:
    root = Path(
        os.environ.get("ATELIER_WORKSPACE_ROOT", str(Path.home() / "Atelier"))
    )
    transcripts = sorted(root.glob("works/*/agents/*/transcript.ndjson"))
    if not transcripts:
        print(f"no transcripts found under {root}/works/*/agents/*/transcript.ndjson")
        return 0
    changed = 0
    for path in transcripts:
        if rewrite(path):
            changed += 1
    print(f"done · {changed}/{len(transcripts)} transcripts updated")
    return 0


def rewrite(path: Path) -> bool:
    """Return True if the file was modified."""
    new_lines: list[str] = []
    modified = False
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            stripped = raw_line.rstrip("\n")
            if not stripped:
                new_lines.append(stripped)
                continue
            try:
                ev = json.loads(stripped)
            except json.JSONDecodeError:
                # Preserve unparseable lines verbatim.
                new_lines.append(stripped)
                continue
            ev_type = ev.get("type")
            if ev_type == "tool_call":
                old_name = ev.get("name", "")
                old_args = ev.get("arguments", {})
                if isinstance(old_args, dict):
                    new_name, new_args = canonicalize_tool(old_name, old_args)
                    if new_name != old_name or new_args != old_args:
                        ev["name"] = new_name
                        ev["arguments"] = new_args
                        modified = True
            elif ev_type == "permission_request":
                old_name = ev.get("tool_name", "")
                old_input = ev.get("tool_input", {})
                if isinstance(old_input, dict):
                    new_name, new_input = canonicalize_tool(old_name, old_input)
                    if new_name != old_name or new_input != old_input:
                        ev["tool_name"] = new_name
                        ev["tool_input"] = new_input
                        modified = True
            new_lines.append(json.dumps(ev, ensure_ascii=False))
    if not modified:
        print(f"unchanged · {path}")
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(f"migrated · {path}")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
