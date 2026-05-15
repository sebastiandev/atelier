"""Port + result type for the periodic "is there an update?" check.

A checker runs ``git fetch`` against the configured remote and compares
the local ``HEAD`` to the remote's main branch. The result is a flat
snapshot the HTTP layer can return without doing any git work itself.

The checker is allowed to fail — the poller swallows errors and the
last successful status (if any) is what the route returns. A first-run
failure leaves ``UpdateStatus`` unset, and the route returns a 200 with
``available=false`` so the UI degrades quietly rather than flashing a
banner on every load.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UpdateStatus:
    """Snapshot of the local checkout vs. its upstream main branch.

    ``available`` is the only field the UI cares about; the rest are
    diagnostic. ``repo_path`` is surfaced so the frontend can show a
    user-copyable path in the popover without an extra round-trip.
    """

    available: bool
    current_sha: str
    latest_sha: str
    repo_path: str


class UpdateChecker(Protocol):
    """Async callable that returns the current update status.

    Implementations are responsible for their own networking (``git
    fetch``) and for handling errors — a return of ``None`` means the
    check couldn't be performed this cycle and the last good status
    should be retained.
    """

    async def __call__(self) -> UpdateStatus | None: ...
