"""Integration tests for /api/connections.

The real `KeyringSecretStore` and `verify` HTTP calls are swapped for
in-memory stubs by overriding `app.state.connection_store` after lifespan
startup — same TestClient fixture path as the rest of the suite, no
keychain prompts, no network calls.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.domain.connections import ConnectionStoreService, VerifyResult
from src.infrastructure.database.connection_repository import SqlConnectionRepository
from src.main import create_app
from src.settings import Settings
from tests.unit.domain.connections._stubs import StubFetcher, StubSecrets, StubVerifier


@pytest.fixture
def stub_verifier() -> StubVerifier:
    return StubVerifier()


@pytest.fixture
def stub_secrets() -> StubSecrets:
    return StubSecrets()


@pytest.fixture
def stub_fetcher() -> StubFetcher:
    return StubFetcher()


@pytest.fixture
def connections_client(
    test_settings: Settings,
    stub_secrets: StubSecrets,
    stub_verifier: StubVerifier,
    stub_fetcher: StubFetcher,
) -> Iterator[TestClient]:
    """TestClient where the ConnectionStore uses the real SQL repo against
    the tmp DB but a stub keychain + stub verifier — no OS keychain
    prompts and no outbound HTTP."""
    app = create_app(test_settings)
    with TestClient(app) as client:
        repo = SqlConnectionRepository(client.app.state.session_factory)
        client.app.state.connection_store = ConnectionStoreService(
            repo, stub_secrets, stub_verifier, stub_fetcher
        )
        yield client


def _create_jira(
    client: TestClient,
    *,
    name: str = "Jira",
    token: str = "secret-jira",
    url: str = "https://example.atlassian.net",
    email: str = "user@example.com",
) -> dict:
    response = client.post(
        "/api/connections",
        json={
            "name": name,
            "token": token,
            "config": {"type": "jira", "url": url, "email": email},
        },
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Create + read
# ---------------------------------------------------------------------------


def test_create_returns_metadata_without_token(
    connections_client: TestClient, stub_secrets: StubSecrets
) -> None:
    body = _create_jira(connections_client)
    assert body["slug"] == "con-1"
    assert body["config"]["type"] == "jira"
    assert body["config"]["url"] == "https://example.atlassian.net"
    assert body["config"]["email"] == "user@example.com"
    assert body["name"] == "Jira"
    assert body["verified"] is False
    assert body["last_used"] is None
    # The defining invariant: no token field anywhere on the read shape.
    assert "token" not in body
    # Token landed in the (stub) keychain, not in the response.
    assert stub_secrets.get("con-1") == "secret-jira"


def test_get_omits_token_field(connections_client: TestClient) -> None:
    _create_jira(connections_client)
    response = connections_client.get("/api/connections/con-1")
    assert response.status_code == 200
    body = response.json()
    assert "token" not in body
    assert body["slug"] == "con-1"


def test_get_404_for_unknown(connections_client: TestClient) -> None:
    response = connections_client.get("/api/connections/con-404")
    assert response.status_code == 404


def test_list_returns_summaries(connections_client: TestClient) -> None:
    _create_jira(connections_client, name="Jira A")
    _create_jira(connections_client, name="Jira B")
    response = connections_client.get("/api/connections")
    assert response.status_code == 200
    body = response.json()
    assert [c["name"] for c in body] == ["Jira A", "Jira B"]
    assert all("token" not in c for c in body)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_patch_metadata_does_not_touch_token(
    connections_client: TestClient, stub_secrets: StubSecrets
) -> None:
    _create_jira(connections_client)
    initial_token = stub_secrets.get("con-1")

    response = connections_client.patch(
        "/api/connections/con-1", json={"name": "Renamed"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert stub_secrets.get("con-1") == initial_token


def test_patch_with_token_rotates_keychain(
    connections_client: TestClient, stub_secrets: StubSecrets
) -> None:
    _create_jira(connections_client)
    response = connections_client.patch(
        "/api/connections/con-1", json={"token": "rotated"}
    )
    assert response.status_code == 200
    # Token still doesn't echo back.
    assert "token" not in response.json()
    assert stub_secrets.get("con-1") == "rotated"


def test_patch_404_for_unknown(connections_client: TestClient) -> None:
    response = connections_client.patch(
        "/api/connections/con-404", json={"name": "X"}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_removes_row_and_keychain_entry(
    connections_client: TestClient, stub_secrets: StubSecrets
) -> None:
    _create_jira(connections_client)
    response = connections_client.delete("/api/connections/con-1")
    assert response.status_code == 204
    # Row is gone.
    assert connections_client.get("/api/connections/con-1").status_code == 404
    # Keychain delete was issued for the slug.
    assert "con-1" in stub_secrets.deletes


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def test_verify_success_flips_verified_and_stamps_last_used(
    connections_client: TestClient, stub_verifier: StubVerifier
) -> None:
    _create_jira(connections_client)
    stub_verifier.queue(VerifyResult(verified=True))

    response = connections_client.post("/api/connections/con-1/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is True
    assert body["error"] is None
    # Persisted: subsequent GET shows verified + last_used.
    refreshed = connections_client.get("/api/connections/con-1").json()
    assert refreshed["verified"] is True
    assert refreshed["last_used"] is not None


def test_verify_failure_persists_unverified(
    connections_client: TestClient, stub_verifier: StubVerifier
) -> None:
    _create_jira(connections_client)
    stub_verifier.queue(VerifyResult(verified=False, error="HTTP 401"))

    response = connections_client.post("/api/connections/con-1/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is False
    assert body["error"] == "HTTP 401"
    refreshed = connections_client.get("/api/connections/con-1").json()
    assert refreshed["verified"] is False
    assert refreshed["last_used"] is None


def test_verify_404_for_unknown(connections_client: TestClient) -> None:
    response = connections_client.post("/api/connections/con-404/verify")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_create_requires_token(connections_client: TestClient) -> None:
    response = connections_client.post(
        "/api/connections",
        json={"name": "Jira", "config": {"type": "jira", "url": "https://x", "email": "a@b"}},
    )
    assert response.status_code == 422


def test_create_rejects_unknown_type(connections_client: TestClient) -> None:
    response = connections_client.post(
        "/api/connections",
        json={"name": "x", "token": "y", "config": {"type": "linear", "url": "x"}},
    )
    assert response.status_code == 422


def test_create_rejects_jira_without_required_fields(
    connections_client: TestClient,
) -> None:
    """Jira config requires url + email — partial config is rejected at
    422 by the discriminated union."""
    response = connections_client.post(
        "/api/connections",
        json={
            "name": "x",
            "token": "y",
            "config": {"type": "jira", "url": "https://x"},  # missing email
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Type descriptors
# ---------------------------------------------------------------------------


def test_list_connection_types_returns_descriptors(
    connections_client: TestClient,
) -> None:
    response = connections_client.get("/api/connections/types")
    assert response.status_code == 200
    body = response.json()
    types = {d["type"] for d in body}
    assert types == {"jira", "sentry", "honeycomb"}


def test_descriptor_shape(connections_client: TestClient) -> None:
    """Smoke-tests the wire shape: each descriptor exposes label, glyph,
    docs, config_fields, and the two capability flags. Jira and Sentry
    are both fetchable; Honeycomb only verifies (no fetcher yet)."""
    body = connections_client.get("/api/connections/types").json()
    by_type = {d["type"]: d for d in body}

    jira = by_type["jira"]
    assert jira["label"] == "Jira"
    assert jira["glyph"] == "JR"
    assert jira["verifiable"] is True
    assert jira["context_fetchable"] is True
    field_ids = {f["id"] for f in jira["config_fields"]}
    assert field_ids == {"url", "email"}

    assert by_type["sentry"]["context_fetchable"] is True
    assert by_type["honeycomb"]["context_fetchable"] is False


def test_descriptor_field_carries_required_attribute(
    connections_client: TestClient,
) -> None:
    """Required-flag round-trips through the descriptor wire shape.
    Sentry has a single required ``org`` field after dropping ``region``."""
    body = connections_client.get("/api/connections/types").json()
    sentry = next(d for d in body if d["type"] == "sentry")
    fields_by_id = {f["id"]: f for f in sentry["config_fields"]}

    assert set(fields_by_id) == {"org"}
    assert fields_by_id["org"]["required"] is True
