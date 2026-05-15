"""Port + URL parser for refreshing PR statuses from a remote.

The fetcher is an async callable that maps a parsed PR ref to its
current status (or ``None`` when the remote can't be reached / the PR
no longer exists). The poller calls one fetcher per non-terminal PR
artifact and writes back any status that changed.

Today there's a single concrete implementation in
``infrastructure/artifacts/github_pr_status.py``. The Protocol keeps
the command layer testable against stub fetchers and leaves room for a
future GitLab / Bitbucket adapter without rewriting the poller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from src.domain.artifacts.status import PR_STATUSES


@dataclass(frozen=True)
class PrRef:
    """Parsed reference to a remote pull request. Today GitHub-only;
    ``host`` is preserved so a future adapter can branch on it without
    changing the parser's return shape."""

    host: str  # e.g. "github.com"
    owner: str
    repo: str
    number: int


# GitHub PR URLs come in a handful of well-known shapes; the canonical
# form is ``https://github.com/<owner>/<repo>/pull/<number>`` (the agent
# is told to record this exact form via ``gh pr create``'s output).
# Be lenient about trailing slashes, fragments, query strings, and
# scheme — agents sometimes paste the URL without ``https://``.
_GITHUB_URL_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?(?P<host>github\.com)/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
    r"(?:[/?#].*)?$",
    re.IGNORECASE,
)


def parse_pr_url(url: str) -> PrRef | None:
    """Extract host + owner + repo + number from a PR URL.

    Returns ``None`` for non-GitHub URLs or malformed input — the
    poller skips those silently. Validates the number is positive (a
    PR #0 doesn't exist in GitHub's numbering).
    """
    if not isinstance(url, str):
        return None
    match = _GITHUB_URL_PATTERN.match(url.strip())
    if match is None:
        return None
    number = int(match.group("number"))
    if number <= 0:
        return None
    return PrRef(
        host=match.group("host").lower(),
        owner=match.group("owner"),
        repo=match.group("repo"),
        number=number,
    )


@dataclass(frozen=True)
class FetchedPrState:
    """Result of one PR fetch.

    ``not_modified`` is True when the remote returned 304 (GitHub
    confirmed our cached state is current); in that case ``status``
    carries no new information and the caller should leave the row
    alone. ``etag`` reflects the freshest cache validator either way
    — a new one on 200, the previously-stored one on 304, or ``None``
    when the remote didn't supply one.
    """

    status: str | None
    etag: str | None
    not_modified: bool


class PrStateFetcher(Protocol):
    """Look up a PR's current status with optional ETag round-trip.

    Returns ``None`` when the remote is unreachable, the PR is gone,
    or auth is missing — the poller treats ``None`` as "leave the row
    alone, try again next cycle". Returns a ``FetchedPrState`` with
    ``not_modified=True`` for a 304 (no state change since
    ``if_none_match``), or with ``not_modified=False`` and a
    populated ``status`` + new ``etag`` for a 200.
    """

    async def __call__(
        self, ref: PrRef, *, if_none_match: str | None = None
    ) -> "FetchedPrState | None": ...


def is_terminal_pr_status(status: str) -> bool:
    """``merged`` and ``closed`` are absorbing states — once a PR
    reaches them, GitHub never moves it back to ``open`` (re-opening a
    PR creates a new event but the state column transitions back to
    open, which would re-add the row to the polling set anyway via the
    next agent-recorded update). The poller uses this to skip rows that
    don't need a refresh."""
    return status in {"merged", "closed"}


__all__ = [
    "PR_STATUSES",
    "FetchedPrState",
    "PrRef",
    "PrStateFetcher",
    "is_terminal_pr_status",
    "parse_pr_url",
]
