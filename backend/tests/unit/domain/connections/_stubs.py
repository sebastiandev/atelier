"""In-memory stubs implementing the ConnectionStore-side ports."""

from __future__ import annotations

from src.domain.connections.dtos import ContextFetchError, VerifyResult
from src.domain.models import Connection, Context


class StubRepository:
    def __init__(self) -> None:
        self.connections: dict[str, Connection] = {}
        self._next_id = 1

    def add(self, connection: Connection) -> Connection:
        connection.id = self._next_id
        self._next_id += 1
        connection.slug = f"con-{connection.id}"
        self.connections[connection.slug] = connection
        return connection

    def upsert(self, connection: Connection) -> Connection:
        if connection.slug is None:
            raise ValueError("upsert requires slug")
        self.connections[connection.slug] = connection
        return connection

    def delete_by_slug(self, slug: str) -> None:
        self.connections.pop(slug, None)

    def get_by_slug(self, slug: str) -> Connection | None:
        return self.connections.get(slug)

    def list_all(self) -> list[Connection]:
        return list(self.connections.values())


class StubSecrets:
    """In-memory key/value secret store. Tracks delete calls so tests can
    assert the keychain entry was removed."""

    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}
        self.deletes: list[str] = []

    def get(self, key: str) -> str | None:
        return self.secrets.get(key)

    def set(self, key: str, value: str) -> None:
        self.secrets[key] = value

    def delete(self, key: str) -> None:
        self.deletes.append(key)
        self.secrets.pop(key, None)


class StubVerifier:
    """Replays a queued list of VerifyResult values, recording the
    connection slug + token presented on each call."""

    def __init__(self, results: list[VerifyResult] | None = None) -> None:
        self._results = list(results or [])
        self.calls: list[tuple[str, str]] = []

    def queue(self, *results: VerifyResult) -> None:
        self._results.extend(results)

    def __call__(self, connection: object, token: str) -> VerifyResult:
        slug = getattr(connection, "slug", "?")
        assert isinstance(slug, str)
        self.calls.append((slug, token))
        if not self._results:
            return VerifyResult(verified=True)
        return self._results.pop(0)


class StubFetcher:
    """Replays a queued list of bodies (str) or errors (Exception). Tests
    enqueue what they want returned for each fetch call; the stub records
    every (slug, value, token) it saw."""

    def __init__(self) -> None:
        self._results: list[str | Exception] = []
        self.calls: list[tuple[str, str, str]] = []

    def queue(self, *results: str | Exception) -> None:
        self._results.extend(results)

    def __call__(self, connection: Connection, context: Context, token: str) -> str:
        slug = connection.slug or "?"
        self.calls.append((slug, context.value, token))
        if not self._results:
            raise ContextFetchError("no result queued")
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
