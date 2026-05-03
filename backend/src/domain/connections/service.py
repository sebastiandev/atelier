"""ConnectionStoreService — composes ConnectionRepository, SecretStore,
ConnectionVerifier into the public ConnectionStore port.

Token handling rule: the token enters the service exclusively through
``CreateConnectionRequest.token`` and ``UpdateConnectionRequest.token``,
is written to the secret store, and never leaves the service through
any read path. The keychain key is the connection slug.

`verified` is owned by the verify path. Update can rotate metadata or
the token, but it cannot stamp `verified` directly — flipping it back
to `false` happens implicitly when a verify call fails.
"""

from datetime import UTC, datetime

from src.domain.connections.dtos import (
    CreateConnectionRequest,
    UpdateConnectionRequest,
    VerifyResult,
)
from src.domain.connections.ports import (
    ConnectionRepository,
    ConnectionVerifier,
    SecretStore,
)
from src.domain.models import Connection


class ConnectionStoreService:
    def __init__(
        self,
        repository: ConnectionRepository,
        secrets: SecretStore,
        verifier: ConnectionVerifier,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._verifier = verifier

    def create(self, req: CreateConnectionRequest) -> Connection:
        connection = Connection(
            type=req.type,
            name=req.name,
            created_at=_now(),
            url=req.url,
            org=req.org,
            region=req.region,
            env=req.env,
            team=req.team,
            email=req.email,
            verified=False,
            last_used=None,
        )
        stored = self._repository.add(connection)
        assert stored.slug is not None
        # Token write follows the row insert: if we crash between the
        # two, reconcile (or a manual delete) cleans up the orphan row.
        # The reverse order would leak a secret with no visible record.
        self._secrets.set(stored.slug, req.token)
        return stored

    def get(self, slug: str) -> Connection | None:
        return self._repository.get_by_slug(slug)

    def list_all(self) -> list[Connection]:
        return self._repository.list_all()

    def update(self, req: UpdateConnectionRequest) -> Connection:
        existing = self._repository.get_by_slug(req.slug)
        if existing is None:
            raise ValueError(f"connection not found: {req.slug}")
        if req.name is not None:
            existing.name = req.name
        if req.url is not None:
            existing.url = req.url
        if req.org is not None:
            existing.org = req.org
        if req.region is not None:
            existing.region = req.region
        if req.env is not None:
            existing.env = req.env
        if req.team is not None:
            existing.team = req.team
        if req.email is not None:
            existing.email = req.email
        if req.token is not None:
            self._secrets.set(req.slug, req.token)
        return self._repository.upsert(existing)

    def delete(self, slug: str) -> None:
        # Secret first, then row. If we crash between, reconcile sees a
        # row with no secret and verify will surface the missing-token
        # error; safer than a row-less secret hanging around in the
        # keychain forever.
        self._secrets.delete(slug)
        self._repository.delete_by_slug(slug)

    def verify(self, slug: str) -> VerifyResult:
        existing = self._repository.get_by_slug(slug)
        if existing is None:
            raise ValueError(f"connection not found: {slug}")
        token = self._secrets.get(slug)
        if token is None:
            existing.verified = False
            self._repository.upsert(existing)
            return VerifyResult(verified=False, error="no token in keychain")
        result = self._verifier(existing, token)
        existing.verified = result.verified
        if result.verified:
            existing.last_used = _now()
        self._repository.upsert(existing)
        return result


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = ["ConnectionStoreService"]
