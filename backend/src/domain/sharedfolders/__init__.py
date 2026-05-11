"""Shared folders domain — persistent cross-agent context per project.

Bridges "uncommitted personal planning" content (BMAD outputs, runbooks,
scratch notes) across every agent in a project. Outlives individual
agent worktrees and is invisible to git — the third bucket of agent
context alongside source repo (committed) and per-agent worktree
(transient scratch).
"""

from src.domain.sharedfolders.dtos import (
    CreateExistingShareRequest,
    CreateNewShareRequest,
    ShareRecord,
    ShareSummary,
    UpdateShareRequest,
)
from src.domain.sharedfolders.ports import (
    MountConflict,
    ShareProvisioner,
    ShareRepository,
    SharedFolderStore,
)
from src.domain.sharedfolders.service import SharedFolderStoreService
from src.domain.sharedfolders.validation import (
    InvalidMountPath,
    validate_mount_path,
)

__all__ = [
    "CreateExistingShareRequest",
    "CreateNewShareRequest",
    "InvalidMountPath",
    "MountConflict",
    "ShareProvisioner",
    "ShareRecord",
    "ShareRepository",
    "ShareSummary",
    "SharedFolderStore",
    "SharedFolderStoreService",
    "UpdateShareRequest",
    "validate_mount_path",
]
