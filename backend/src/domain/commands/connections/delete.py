"""Delete a Connection: removes both the SQLite row and the keychain entry."""

from src.domain.connections import ConnectionStore


def execute(store: ConnectionStore, slug: str) -> None:
    store.delete(slug)
