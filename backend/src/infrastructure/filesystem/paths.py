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

    def chats_dir(self) -> Path:
        return self.workspace_root / "chats"

    def chat_dir(self, chat_slug: str) -> Path:
        _validate_slug(chat_slug)
        return self.chats_dir() / chat_slug

    def chat_json(self, chat_slug: str) -> Path:
        return self.chat_dir(chat_slug) / "chat.json"

    def chat_transcript(self, chat_slug: str) -> Path:
        return self.chat_dir(chat_slug) / "transcript.ndjson"

    def chat_compactions_dir(self, chat_slug: str) -> Path:
        return self.chat_dir(chat_slug) / "compactions"

    def work_dir(self, work_slug: str) -> Path:
        _validate_slug(work_slug)
        return self.works_dir() / work_slug

    def work_json(self, work_slug: str) -> Path:
        return self.work_dir(work_slug) / "work.json"

    def projects_dir(self) -> Path:
        return self.workspace_root / "projects"

    def project_dir(self, project_slug: str) -> Path:
        _validate_slug(project_slug)
        return self.projects_dir() / project_slug

    def project_json(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "project.json"

    def project_shared_dir(self, project_slug: str) -> Path:
        """Parent dir of all the project's shared folders. Holds either
        real subdirs (default-location shares) or symlinks (custom-
        location shares)."""
        return self.project_dir(project_slug) / "shared"

    def project_share_dir(self, project_slug: str, share_slug: str) -> Path:
        """Canonical path for one share. May resolve to a real dir or a
        symlink to a user-chosen location elsewhere on disk."""
        _validate_slug(share_slug)
        return self.project_shared_dir(project_slug) / share_slug

    def brief(self, work_slug: str) -> Path:
        return self.work_dir(work_slug) / "brief.md"

    def work_chat_context_dir(self, work_slug: str, folder_name: str) -> Path:
        _validate_slug(folder_name)
        return self.work_dir(work_slug) / "chat-contexts" / folder_name

    def work_chat_context_file(
        self, work_slug: str, folder_name: str, filename: str
    ) -> Path:
        return self.work_chat_context_dir(work_slug, folder_name) / filename

    def agent_dir(self, work_slug: str, agent_slug: str) -> Path:
        _validate_slug(agent_slug)
        return self.work_dir(work_slug) / "agents" / agent_slug

    def worktree_dir(self, work_slug: str, agent_slug: str) -> Path:
        """Per-agent git worktree directory. Provisioned by the
        WorktreeManager when the work's source folder is a git repo;
        absent on disk when the source is a plain directory (in which
        case the agent's adapter cwd is the source folder itself)."""
        _validate_slug(agent_slug)
        return self.work_dir(work_slug) / "worktrees" / agent_slug

    def transcript(self, work_slug: str, agent_slug: str) -> Path:
        return self.agent_dir(work_slug, agent_slug) / "transcript.ndjson"

    def agent_context_dir(self, work_slug: str, agent_slug: str) -> Path:
        return self.agent_dir(work_slug, agent_slug) / "context"

    def agent_context_index(self, work_slug: str, agent_slug: str) -> Path:
        return self.agent_dir(work_slug, agent_slug) / "context.md"

    def agent_compactions_dir(self, work_slug: str, agent_slug: str) -> Path:
        return self.agent_dir(work_slug, agent_slug) / "compactions"


__all__ = ["WorkspacePaths"]
