"""GitHub adapter for ``PrStateFetcher``.

Talks to ``https://api.github.com`` over httpx. Auth comes from the
user's local ``gh`` CLI (``gh auth token``) — no new credential
surface to manage, and the user already has ``gh`` configured because
that's what agents shell out to when creating PRs in the first place.

Maps GitHub's three booleans (``state`` / ``merged`` / ``draft``) onto
Atelier's ``PrStatus`` vocabulary:

    merged == True                  → "merged"
    state == "closed" and !merged   → "closed"
    state == "open"  and draft      → "draft"
    state == "open"  and !draft     → "open"

Every error path returns ``None`` — auth missing, network down, 404,
5xx, schema drift. The caller (the poller) treats ``None`` as "skip
this row this cycle"; the periodic loop self-heals as soon as the
underlying condition clears.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import Any

import httpx

from src.domain.artifacts.pr_status import PrRef


# Pluggable so tests / future "GitHub connection" wiring can swap in a
# token source without monkey-patching the ``gh`` shell-out.
TokenSupplier = Callable[[], "str | None"]

_log = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
# GitHub asks clients to send the API version + a vendored Accept;
# omitting them works but the headers are documented best-practice and
# cost nothing.
_HEADERS_BASE = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "Atelier-PR-Status-Poller",
}
# Per-request timeout. The poll cadence is 5 min and we fan out with a
# small concurrency cap, so a slow GitHub shouldn't drag the whole
# cycle past its window. 10s is generous for a single PR fetch.
_REQUEST_TIMEOUT_SECONDS = 10.0


class GitHubPrStateFetcher:
    """Concrete ``PrStateFetcher`` implementation. Construct with a
    shared ``httpx.AsyncClient`` (the poller owns the client's
    lifecycle) and an optional token-supplier so tests can inject one
    without monkey-patching ``gh``."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        token_supplier: TokenSupplier | None = None,
    ) -> None:
        self._client = client
        self._token_supplier = token_supplier or _gh_auth_token

    async def __call__(self, ref: PrRef) -> str | None:
        # Re-fetch the token each call rather than cache once. ``gh auth``
        # state can change (logout, scope refresh) and the cost is one
        # subprocess invocation every ~5 minutes — negligible.
        token = self._token_supplier()
        if token is None:
            _log.debug("no gh auth token; skipping PR %s/%s#%d",
                       ref.owner, ref.repo, ref.number)
            return None
        if ref.host != "github.com":
            # Only GitHub URLs reach here today; defensive guard so a
            # future parser change can't silently send GitLab refs at
            # the GitHub API.
            return None
        url = f"{_GITHUB_API}/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}"
        headers = {**_HEADERS_BASE, "Authorization": f"Bearer {token}"}
        try:
            response = await self._client.get(
                url, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS
            )
        except httpx.HTTPError as exc:
            _log.warning("GitHub fetch failed for %s: %s", url, exc)
            return None
        if response.status_code == 404:
            # PR was deleted (or repo renamed). Leave the row alone —
            # surfacing as "still open" is wrong but flipping it to
            # "closed" is also wrong. Caller skips.
            _log.info("PR %s/%s#%d returned 404; leaving row alone",
                      ref.owner, ref.repo, ref.number)
            return None
        if response.status_code >= 400:
            _log.warning(
                "GitHub returned %d for %s: %s",
                response.status_code, url, response.text[:200],
            )
            return None
        try:
            payload = response.json()
        except ValueError:
            _log.warning("GitHub response wasn't JSON for %s", url)
            return None
        return _map_github_state(payload)


def _map_github_state(payload: dict[str, Any]) -> str | None:
    """GitHub's API returns booleans + a tri-state string; map onto
    Atelier's flat status enum. Unknown shapes return ``None`` so the
    poller leaves the row alone (better than a wrong overwrite)."""
    state = payload.get("state")
    merged = bool(payload.get("merged"))
    draft = bool(payload.get("draft"))
    if merged:
        return "merged"
    if state == "closed":
        return "closed"
    if state == "open":
        return "draft" if draft else "open"
    return None


def _gh_auth_token() -> str | None:
    """Read the GitHub token the user is logged into ``gh`` with.

    Returns ``None`` if ``gh`` isn't installed or the user isn't logged
    in — the poller treats that as "no PRs to refresh right now" and
    exits cleanly. We deliberately don't fall back to ``GITHUB_TOKEN``
    from env: agents may have very narrow tokens scoped to creating PRs
    that won't authorise reading them, and silently using the wrong
    token would produce confusing 401s.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        _log.debug("gh auth token unavailable: %s", exc)
        return None
    if result.returncode != 0:
        _log.debug("gh auth token returned %d: %s",
                   result.returncode, result.stderr.strip())
        return None
    token = result.stdout.strip()
    return token or None


__all__ = ["GitHubPrStateFetcher"]
