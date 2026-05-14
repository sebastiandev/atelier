"""Tests for the GitHub adapter — status mapping and fetcher behavior.

The mapping is the most important contract to lock down: GitHub
returns three independent fields (``state``, ``merged``, ``draft``)
and we collapse them onto Atelier's flat PrStatus enum. Getting that
wrong silently mis-reports merged PRs as open.
"""

from __future__ import annotations

import httpx
import pytest

from src.domain.artifacts.pr_status import PrRef
from src.infrastructure.artifacts.github_pr_status import (
    GitHubPrStateFetcher,
    _map_github_state,
)


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"state": "closed", "merged": True, "draft": False}, "merged"),
        ({"state": "closed", "merged": False, "draft": False}, "closed"),
        ({"state": "open", "merged": False, "draft": True}, "draft"),
        ({"state": "open", "merged": False, "draft": False}, "open"),
        # merged trumps everything — even an "open + merged" mongrel
        # would be reported as merged.
        ({"state": "open", "merged": True, "draft": True}, "merged"),
    ],
)
def test_map_github_state_dispatch(payload: dict, expected: str) -> None:
    assert _map_github_state(payload) == expected


def test_map_github_state_returns_none_on_unknown_shape() -> None:
    """Schema drift / unexpected state values must not produce a wrong
    overwrite. ``None`` tells the caller to leave the row alone."""
    assert _map_github_state({"state": "merged"}) is None
    assert _map_github_state({}) is None


@pytest.mark.anyio
async def test_fetcher_returns_none_without_token() -> None:
    """No gh login → no fetch attempted, return None. The poller treats
    this as 'no PRs to refresh right now' and exits cleanly."""
    async with httpx.AsyncClient() as client:
        fetcher = GitHubPrStateFetcher(client, token_supplier=lambda: None)
        result = await fetcher(
            PrRef(host="github.com", owner="o", repo="r", number=1)
        )
    assert result is None


@pytest.mark.anyio
async def test_fetcher_skips_non_github_host() -> None:
    """Defensive guard: even with a token, the fetcher only hits
    api.github.com. A future GitLab parser must NOT accidentally send
    GitLab refs through this adapter."""
    async with httpx.AsyncClient() as client:
        fetcher = GitHubPrStateFetcher(client, token_supplier=lambda: "tok")
        result = await fetcher(
            PrRef(host="gitlab.com", owner="o", repo="r", number=1)
        )
    assert result is None


@pytest.mark.anyio
async def test_fetcher_maps_github_response() -> None:
    """End-to-end mapping: the adapter's __call__ should land on
    ``"merged"`` when GitHub reports a merged PR. We swap httpx's
    transport for a mock so the test stays offline."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/o/r/pulls/123"
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(
            200,
            json={"state": "closed", "merged": True, "draft": False},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = GitHubPrStateFetcher(client, token_supplier=lambda: "tok")
        result = await fetcher(
            PrRef(host="github.com", owner="o", repo="r", number=123)
        )
    assert result == "merged"


@pytest.mark.anyio
async def test_fetcher_returns_none_on_404() -> None:
    """A deleted PR (or renamed repo) must not flip the row to
    closed — that'd lie. Return None and leave the row alone."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = GitHubPrStateFetcher(client, token_supplier=lambda: "tok")
        result = await fetcher(
            PrRef(host="github.com", owner="o", repo="r", number=999)
        )
    assert result is None


@pytest.mark.anyio
async def test_fetcher_returns_none_on_500() -> None:
    """Transient GitHub errors: skip this cycle, try again next."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = GitHubPrStateFetcher(client, token_supplier=lambda: "tok")
        result = await fetcher(
            PrRef(host="github.com", owner="o", repo="r", number=1)
        )
    assert result is None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
