"""Ports for the ConnectionStore boundary.

`ConnectionStore` is the public port that commands depend on. The other
three decompose its implementation:

  - `ConnectionRepository` — SQLite-side row operations on metadata.
  - `SecretStore` — opaque key/value secret storage (OS keychain in prod).
  - `ConnectionVerifier` — calls a source's auth endpoint to confirm a
    token works, returning `VerifyResult`.

`ConnectionStoreService` (in `service.py`) implements `ConnectionStore`
using the three. Domain stays framework-free — these Protocols expose
only stdlib + domain types.
"""

from typing import Protocol

from src.domain.connections.dtos import (
    CreateConnectionRequest,
    UpdateConnectionRequest,
    VerifyResult,
)
from src.domain.models import Connection


class ConnectionRepository(Protocol):
    """SQLite-side operations on Connection metadata. No tokens — ever."""

    def add(self, connection: Connection) -> Connection: ...

    def upsert(self, connection: Connection) -> Connection: ...

    def delete_by_slug(self, slug: str) -> None: ...

    def get_by_slug(self, slug: str) -> Connection | None: ...

    def list_all(self) -> list[Connection]: ...


class SecretStore(Protocol):
    """Opaque key/value secret storage. ``key`` is the connection slug;
    callers never see the keychain naming scheme."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class ConnectionVerifier(Protocol):
    """Hits the source's auth endpoint to check the token. The
    implementation dispatches on ``connection.type``."""

    def __call__(self, connection: Connection, token: str) -> VerifyResult: ...


class ConnectionStore(Protocol):
    """Public persistence boundary for connection metadata + tokens."""

    def create(self, req: CreateConnectionRequest) -> Connection: ...

    def get(self, slug: str) -> Connection | None: ...

    def list_all(self) -> list[Connection]: ...

    def update(self, req: UpdateConnectionRequest) -> Connection: ...

    def delete(self, slug: str) -> None: ...

    def verify(self, slug: str) -> VerifyResult: ...
