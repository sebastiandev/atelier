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
    ContextFetchError,
    CreateConnectionRequest,
    UpdateConnectionRequest,
    VerifyResult,
)
from src.domain.connections.ports import (
    ConnectionRepository,
    ConnectionVerifier,
    ContextFetcher,
    SecretStore,
)
from src.domain.models import Connection, Context


class ConnectionStoreService:
    def __init__(
        self,
        repository: ConnectionRepository,
        secrets: SecretStore,
        verifier: ConnectionVerifier,
        fetcher: ContextFetcher,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._verifier = verifier
        self._fetcher = fetcher

    def create(self, req: CreateConnectionRequest) -> Connection:
        connection = Connection(
            type=req.type,
            name=req.name,
            created_at=_now(),
            config=req.config,
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
        if req.config is not None:
            existing.config = req.config
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

    def fetch_context_body(self, context: Context) -> str:
        if not context.conn_id:
            raise ContextFetchError(
                f"context type {context.type!r} requires a connection (conn_id)"
            )
        connection = self._repository.get_by_slug(context.conn_id)
        if connection is None:
            raise ContextFetchError(
                f"connection not found: {context.conn_id}"
            )
        token = self._secrets.get(context.conn_id)
        if token is None:
            raise ContextFetchError(
                f"no token in keychain for connection: {context.conn_id}"
            )
        body = self._fetcher(connection, context, token)
        # A successful fetch is a real use of the credential — same
        # treatment as a successful verify.
        connection.last_used = _now()
        self._repository.upsert(connection)
        return body


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = ["ConnectionStoreService"]
