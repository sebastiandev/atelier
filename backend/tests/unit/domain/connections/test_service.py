"""Unit tests for ConnectionStoreService against in-memory stubs.

Token handling is the load-bearing invariant: secrets enter via
CreateConnectionRequest/UpdateConnectionRequest only, and never come
back out through any read path.
"""

import pytest

from src.domain.connections import (
    ConnectionStoreService,
    ContextFetchError,
    CreateConnectionRequest,
    JiraConfig,
    UpdateConnectionRequest,
    VerifyResult,
)
from src.domain.models import Context
from tests.unit.domain.connections._stubs import (
    StubFetcher,
    StubRepository,
    StubSecrets,
    StubVerifier,
)


def _make_service() -> tuple[
    ConnectionStoreService, StubRepository, StubSecrets, StubVerifier, StubFetcher
]:
    repo = StubRepository()
    secrets = StubSecrets()
    verifier = StubVerifier()
    fetcher = StubFetcher()
    service = ConnectionStoreService(repo, secrets, verifier, fetcher)
    return service, repo, secrets, verifier, fetcher


def _create_jira(service: ConnectionStoreService, *, name: str = "Jira") -> str:
    conn = service.create(
        CreateConnectionRequest(
            type="jira",
            name=name,
            token="secret-token",
            config=JiraConfig(
                url="https://example.atlassian.net",
                email="user@example.com",
            ),
        )
    )
    assert conn.slug is not None
    return conn.slug


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_persists_metadata_and_writes_keychain() -> None:
    service, repo, secrets, _, _ = _make_service()
    slug = _create_jira(service)
    assert slug == "con-1"
    row = repo.get_by_slug(slug)
    assert row is not None
    assert row.type == "jira"
    assert row.name == "Jira"
    assert isinstance(row.config, JiraConfig)
    assert row.config.url == "https://example.atlassian.net"
    assert row.config.email == "user@example.com"
    assert row.verified is False
    assert row.last_used is None
    assert secrets.get(slug) == "secret-token"


def test_created_connection_has_no_token_field() -> None:
    """The Connection entity is the public read shape — it must not
    carry the token on any read path."""
    service, _, _, _, _ = _make_service()
    slug = _create_jira(service)
    row = service.get(slug)
    assert row is not None
    assert not hasattr(row, "token")


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_metadata_does_not_touch_secret() -> None:
    service, _, secrets, _, _ = _make_service()
    slug = _create_jira(service)
    initial_token = secrets.get(slug)

    service.update(UpdateConnectionRequest(slug=slug, name="Renamed"))

    row = service.get(slug)
    assert row is not None
    assert row.name == "Renamed"
    assert secrets.get(slug) == initial_token


def test_update_with_token_rotates_keychain() -> None:
    service, _, secrets, _, _ = _make_service()
    slug = _create_jira(service)

    service.update(UpdateConnectionRequest(slug=slug, token="rotated"))

    assert secrets.get(slug) == "rotated"


def test_update_unknown_slug_raises() -> None:
    service, _, _, _, _ = _make_service()
    with pytest.raises(ValueError, match="not found"):
        service.update(UpdateConnectionRequest(slug="con-999", name="X"))


def test_update_with_config_replaces_typed_config() -> None:
    service, _, _, _, _ = _make_service()
    slug = _create_jira(service)

    new_config = JiraConfig(
        url="https://other.atlassian.net", email="someone@other.com"
    )
    service.update(UpdateConnectionRequest(slug=slug, config=new_config))

    row = service.get(slug)
    assert row is not None
    assert isinstance(row.config, JiraConfig)
    assert row.config.url == "https://other.atlassian.net"
    assert row.config.email == "someone@other.com"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_removes_row_and_secret() -> None:
    service, repo, secrets, _, _ = _make_service()
    slug = _create_jira(service)

    service.delete(slug)

    assert repo.get_by_slug(slug) is None
    assert secrets.get(slug) is None
    assert slug in secrets.deletes


def test_delete_unknown_slug_is_noop() -> None:
    service, _, secrets, _, _ = _make_service()
    service.delete("con-404")  # does not raise
    assert "con-404" in secrets.deletes


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def test_verify_success_flips_verified_and_stamps_last_used() -> None:
    service, repo, _, verifier, _ = _make_service()
    slug = _create_jira(service)
    verifier.queue(VerifyResult(verified=True))

    result = service.verify(slug)

    assert result.verified is True
    row = repo.get_by_slug(slug)
    assert row is not None
    assert row.verified is True
    assert row.last_used is not None
    # Verifier saw the token from the keychain.
    assert verifier.calls == [(slug, "secret-token")]


def test_verify_failure_persists_unverified_and_does_not_touch_last_used() -> None:
    service, repo, _, verifier, _ = _make_service()
    slug = _create_jira(service)
    verifier.queue(VerifyResult(verified=False, error="401"))

    result = service.verify(slug)

    assert result.verified is False
    assert result.error == "401"
    row = repo.get_by_slug(slug)
    assert row is not None
    assert row.verified is False
    assert row.last_used is None


def test_verify_with_missing_secret_returns_no_token_error() -> None:
    service, repo, secrets, verifier, _ = _make_service()
    slug = _create_jira(service)
    secrets.secrets.pop(slug)  # simulate keychain wipe

    result = service.verify(slug)

    assert result.verified is False
    assert result.error == "no token in keychain"
    assert verifier.calls == []  # never called
    row = repo.get_by_slug(slug)
    assert row is not None
    assert row.verified is False


def test_verify_unknown_slug_raises() -> None:
    service, _, _, _, _ = _make_service()
    with pytest.raises(ValueError, match="not found"):
        service.verify("con-404")


# ---------------------------------------------------------------------------
# Fetch context body
# ---------------------------------------------------------------------------


def test_fetch_context_body_calls_fetcher_and_stamps_last_used() -> None:
    service, repo, _, _, fetcher = _make_service()
    slug = _create_jira(service)
    fetcher.queue("# ENG-1\n\nbody\n")

    body = service.fetch_context_body(
        Context(type="jira", value="ENG-1", conn_id=slug)
    )

    assert body == "# ENG-1\n\nbody\n"
    # Fetcher saw the (slug, value, token) triple.
    assert fetcher.calls == [(slug, "ENG-1", "secret-token")]
    row = repo.get_by_slug(slug)
    assert row is not None
    assert row.last_used is not None


def test_fetch_context_body_propagates_fetcher_error() -> None:
    service, repo, _, _, fetcher = _make_service()
    slug = _create_jira(service)
    fetcher.queue(ContextFetchError("jira HTTP 500 for ENG-1"))

    with pytest.raises(ContextFetchError, match="HTTP 500"):
        service.fetch_context_body(
            Context(type="jira", value="ENG-1", conn_id=slug)
        )
    # last_used not stamped on failure.
    row = repo.get_by_slug(slug)
    assert row is not None
    assert row.last_used is None


def test_fetch_context_body_missing_conn_id_raises() -> None:
    service, _, _, _, _ = _make_service()
    with pytest.raises(ContextFetchError, match="requires a connection"):
        service.fetch_context_body(Context(type="jira", value="ENG-1"))


def test_fetch_context_body_unknown_connection_raises() -> None:
    service, _, _, _, _ = _make_service()
    with pytest.raises(ContextFetchError, match="connection not found"):
        service.fetch_context_body(
            Context(type="jira", value="ENG-1", conn_id="con-999")
        )


def test_fetch_context_body_missing_token_raises() -> None:
    service, _, secrets, _, _ = _make_service()
    slug = _create_jira(service)
    secrets.secrets.pop(slug)  # simulate keychain wipe

    with pytest.raises(ContextFetchError, match="no token in keychain"):
        service.fetch_context_body(
            Context(type="jira", value="ENG-1", conn_id=slug)
        )
