"""Workspace path computation.

Pure functions over `workspace_root`. No I/O, no mkdir — callers do that
through `atomic` or directly. Every method that accepts a slug validates it
through `_validate_slug` so a malicious or buggy upstream can never coax a
path outside the workspace.
"""

import re
from dataclasses import dataclass
from pathlib import Path

_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_SLUG_LEN = 64


def _validate_slug(slug: str) -> None:
    if not isinstance(slug, str) or not slug:
        raise ValueError(f"invalid slug: {slug!r} (must be non-empty string)")
    if len(slug) > _MAX_SLUG_LEN:
        raise ValueError(f"invalid slug: {slug!r} (max {_MAX_SLUG_LEN} chars)")
    if slug.startswith("."):
        raise ValueError(f"invalid slug: {slug!r} (leading dot)")
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError(f"invalid slug: {slug!r} (allowed: A-Z, a-z, 0-9, '-', '_')")


@dataclass(frozen=True)
class WorkspacePaths:
    """Resolves canonical paths under a workspace root.

    All slug arguments are validated; paths are constructed by joining
    `workspace_root` with sanitised segments only. Direct construction with
    `Path / slug` would silently accept `..` — these methods do not.
    """

    workspace_root: Path

    def works_dir(self) -> Path:
        return self.workspace_root / "works"

    def work_dir(self, work_slug: str) -> Path:
        _validate_slug(work_slug)
        return self.works_dir() / work_slug

    def work_json(self, work_slug: str) -> Path:
        return self.work_dir(work_slug) / "work.json"

    def brief(self, work_slug: str) -> Path:
        return self.work_dir(work_slug) / "brief.md"

    def agent_dir(self, work_slug: str, agent_slug: str) -> Path:
        _validate_slug(agent_slug)
        return self.work_dir(work_slug) / "agents" / agent_slug

    def transcript(self, work_slug: str, agent_slug: str) -> Path:
        return self.agent_dir(work_slug, agent_slug) / "transcript.ndjson"


__all__ = ["WorkspacePaths"]
