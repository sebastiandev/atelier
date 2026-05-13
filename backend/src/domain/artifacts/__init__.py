"""Artifact domain types — typed hierarchy over the unified table.

Three concrete types today, each with its own status vocabulary:

  - ``PrArtifact`` — pull-request lifecycle (draft/open/merged/closed).
  - ``JiraArtifact`` — ticket lifecycle (todo/in_progress/in_review/done/
    closed/blocked).
  - ``DocArtifact`` — document state (draft for shared, pending/committed
    for worktree-resident files; the derived state lands here too).

All share ``BaseArtifact``'s identity/lineage fields. The SA imperative
mapping uses single-table inheritance on the existing ``artifacts``
table — ``polymorphic_on=type`` dispatches each row to the right
subclass on load.

Adding a new artifact type: define the subclass + its status enum here,
register the polymorphic identity in ``infrastructure/database/mapping``,
and add a per-type validator + recorder branch. No table change is
required as long as the existing columns suffice.
"""

from src.domain.artifacts.models import (
    Artifact,
    ArtifactType,
    BaseArtifact,
    DocArtifact,
    DocStatus,
    JiraArtifact,
    JiraStatus,
    PrArtifact,
    PrStatus,
    make_artifact,
)
from src.domain.artifacts.status import (
    DOC_STATUSES,
    JIRA_STATUSES,
    PR_STATUSES,
    InvalidStatus,
    validate_status,
)

__all__ = [
    "Artifact",
    "ArtifactType",
    "BaseArtifact",
    "DOC_STATUSES",
    "DocArtifact",
    "DocStatus",
    "InvalidStatus",
    "JIRA_STATUSES",
    "JiraArtifact",
    "JiraStatus",
    "PR_STATUSES",
    "PrArtifact",
    "PrStatus",
    "make_artifact",
    "validate_status",
]
