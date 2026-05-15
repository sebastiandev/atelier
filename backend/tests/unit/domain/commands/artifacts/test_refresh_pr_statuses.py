"""Tests for the refresh-pr-statuses command.

Stubs the fetcher with a scripted dict so we cover the dispatch
contract without going near GitHub or httpx.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest

from src.domain.artifacts.pr_status import FetchedPrState, PrRef
from src.domain.commands.artifacts import refresh_pr_statuses
from src.domain.workstore import (
    CreateWorkRequest,
    RecordArtifactRequest,
    WorkStoreService,
)
from tests.unit.domain.workstore._stubs import (
    StubFiles,
    StubRepository,
    StubTranscriptLog,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_store() -> WorkStoreService:
    repo = StubRepository()
    files = StubFiles()
    transcript = StubTranscriptLog()
    return WorkStoreService(repo, files, transcript, clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))


def _seed_pr(store: WorkStoreService, *, status: str, url: str) -> str:
    work = store.create_work(
        CreateWorkRequest(name="W", description="d", project_slug=None)
    )
    assert work.work.slug is not None
    artifact = store.record_artifact(
        RecordArtifactRequest(
            work_slug=work.work.slug,
            agent_slug=None,
            type="pr",
            title="t",
            status=status,
            url=url,
        )
    )
    assert artifact.slug is not None
    return artifact.slug


def _scripted_fetcher(
    by_url: dict[str, str | None],
    *,
    etag: str | None = None,
) -> Callable[..., Awaitable[FetchedPrState | None]]:
    """Build a fetcher whose return shape matches ``PrStateFetcher``.

    ``by_url[<url>] = None`` means "skip this row" (network failure);
    a status string yields a ``FetchedPrState`` carrying that status.
    """

    async def fetch(
        ref: PrRef, *, if_none_match: str | None = None
    ) -> FetchedPrState | None:
        key = f"https://github.com/{ref.owner}/{ref.repo}/pull/{ref.number}"
        if key not in by_url:
            return None
        value = by_url[key]
        if value is None:
            return None
        return FetchedPrState(status=value, etag=etag, not_modified=False)

    return fetch


@pytest.mark.anyio
async def test_empty_pool_is_a_clean_noop() -> None:
    """The guard the user explicitly asked for: when no PR artifacts
    exist, the command must not call the fetcher at all."""
    store = _make_store()
    calls = 0

    async def fetcher(
        _ref: PrRef, *, if_none_match: str | None = None
    ) -> FetchedPrState | None:
        nonlocal calls
        calls += 1
        return None

    result = await refresh_pr_statuses.execute(store, fetcher)
    assert result.checked == 0
    assert result.updated == 0
    assert result.skipped == 0
    assert calls == 0


@pytest.mark.anyio
async def test_terminal_prs_are_skipped() -> None:
    """A merged PR sits in the table forever, but the workstore query
    only returns non-terminal rows. The fetcher must not be called for
    rows in terminal states."""
    store = _make_store()
    _seed_pr(store, status="merged", url="https://github.com/o/r/pull/1")
    _seed_pr(store, status="closed", url="https://github.com/o/r/pull/2")

    calls = 0

    async def fetcher(
        _ref: PrRef, *, if_none_match: str | None = None
    ) -> FetchedPrState | None:
        nonlocal calls
        calls += 1
        return "merged"

    result = await refresh_pr_statuses.execute(store, fetcher)
    assert result.checked == 0
    assert calls == 0


@pytest.mark.anyio
async def test_status_change_persists() -> None:
    """The dominant happy path: GitHub reports merged, we flip the row."""
    store = _make_store()
    slug = _seed_pr(
        store, status="open", url="https://github.com/o/r/pull/42"
    )
    fetcher = _scripted_fetcher(
        {"https://github.com/o/r/pull/42": "merged"}
    )

    result = await refresh_pr_statuses.execute(store, fetcher)
    assert result.checked == 1
    assert result.updated == 1
    assert result.skipped == 0
    assert store.get_artifact_by_slug(slug).status == "merged"


@pytest.mark.anyio
async def test_no_change_does_not_write() -> None:
    """If GitHub reports the same status we already have, no update
    happens. The result reflects 'checked but no-op'."""
    store = _make_store()
    slug = _seed_pr(
        store, status="open", url="https://github.com/o/r/pull/3"
    )
    fetcher = _scripted_fetcher({"https://github.com/o/r/pull/3": "open"})

    result = await refresh_pr_statuses.execute(store, fetcher)
    assert result.checked == 1
    assert result.updated == 0
    assert store.get_artifact_by_slug(slug).status == "open"


@pytest.mark.anyio
async def test_fetcher_none_increments_skipped() -> None:
    """Network failure / auth missing → fetcher returns None. The row
    is left untouched and the counter advances so logs are honest."""
    store = _make_store()
    slug = _seed_pr(
        store, status="open", url="https://github.com/o/r/pull/4"
    )
    fetcher = _scripted_fetcher({"https://github.com/o/r/pull/4": None})

    result = await refresh_pr_statuses.execute(store, fetcher)
    assert result.checked == 0
    assert result.updated == 0
    assert result.skipped == 1
    assert store.get_artifact_by_slug(slug).status == "open"


@pytest.mark.anyio
async def test_unparseable_url_is_skipped_without_calling_fetcher() -> None:
    """Non-GitHub PR URLs (e.g. GitLab) end up persisted in the table.
    The poller must skip them locally — never hand them to the
    GitHub-only fetcher."""
    store = _make_store()
    _seed_pr(
        store,
        status="open",
        url="https://gitlab.example.com/o/r/-/merge_requests/1",
    )
    calls = 0

    async def fetcher(
        _ref: PrRef, *, if_none_match: str | None = None
    ) -> FetchedPrState | None:
        nonlocal calls
        calls += 1
        return "merged"

    result = await refresh_pr_statuses.execute(store, fetcher)
    assert result.skipped == 1
    assert calls == 0


@pytest.mark.anyio
async def test_fetcher_exception_does_not_kill_cycle() -> None:
    """A buggy fetcher raising on one PR must not stop the others
    from being checked. The bad row is counted as skipped."""
    store = _make_store()
    _seed_pr(store, status="open", url="https://github.com/o/r/pull/10")
    slug_b = _seed_pr(
        store, status="open", url="https://github.com/o/r/pull/11"
    )

    async def fetcher(
        ref: PrRef, *, if_none_match: str | None = None
    ) -> FetchedPrState | None:
        if ref.number == 10:
            raise RuntimeError("boom")
        return FetchedPrState(status="merged", etag=None, not_modified=False)

    result = await refresh_pr_statuses.execute(store, fetcher)
    assert result.checked == 1
    assert result.updated == 1
    assert result.skipped == 1
    assert store.get_artifact_by_slug(slug_b).status == "merged"


@pytest.mark.anyio
async def test_etag_is_sent_on_subsequent_calls() -> None:
    """After a successful fetch persists an ETag, the next cycle must
    send it as ``If-None-Match`` so GitHub can return 304 without
    burning the rate budget."""
    store = _make_store()
    _seed_pr(store, status="open", url="https://github.com/o/r/pull/50")
    seen_etags: list[str | None] = []

    async def fetcher(
        ref: PrRef, *, if_none_match: str | None = None
    ) -> FetchedPrState | None:
        seen_etags.append(if_none_match)
        return FetchedPrState(
            status="open", etag='"abc"', not_modified=False
        )

    # First pass — no etag stored yet, so we send None.
    await refresh_pr_statuses.execute(store, fetcher)
    # Second pass — first pass should have persisted '"abc"'.
    await refresh_pr_statuses.execute(store, fetcher)
    assert seen_etags == [None, '"abc"']


@pytest.mark.anyio
async def test_304_short_circuits_without_status_write() -> None:
    """A 304 (``not_modified=True``) means the cached status is
    authoritative — no DB write, no ``checked`` bump, but a
    ``not_modified`` count for visibility."""
    store = _make_store()
    slug = _seed_pr(
        store, status="open", url="https://github.com/o/r/pull/51"
    )

    async def fetcher(
        _ref: PrRef, *, if_none_match: str | None = None
    ) -> FetchedPrState | None:
        return FetchedPrState(
            status=None, etag='"unchanged"', not_modified=True
        )

    result = await refresh_pr_statuses.execute(store, fetcher)
    assert result.checked == 0
    assert result.updated == 0
    assert result.not_modified == 1
    assert store.get_artifact_by_slug(slug).status == "open"


@pytest.mark.anyio
async def test_etag_rotation_on_304_persists_new_validator() -> None:
    """Spec allows the server to rotate the ETag while answering 304.
    When that happens, we should persist the fresh validator so the
    next cycle sends the right header."""
    store = _make_store()
    slug = _seed_pr(
        store, status="open", url="https://github.com/o/r/pull/52"
    )
    # Seed the artifact with an existing etag by running one fetch.

    async def initial_fetcher(
        _ref: PrRef, *, if_none_match: str | None = None
    ) -> FetchedPrState | None:
        return FetchedPrState(
            status="open", etag='"v1"', not_modified=False
        )

    await refresh_pr_statuses.execute(store, initial_fetcher)
    assert store.get_artifact_by_slug(slug).pr_etag == '"v1"'

    async def rotating_fetcher(
        _ref: PrRef, *, if_none_match: str | None = None
    ) -> FetchedPrState | None:
        # Server rotated the etag while reporting "still unchanged".
        return FetchedPrState(
            status=None, etag='"v2"', not_modified=True
        )

    await refresh_pr_statuses.execute(store, rotating_fetcher)
    assert store.get_artifact_by_slug(slug).pr_etag == '"v2"'
