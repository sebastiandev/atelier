"""Read one Connection by slug."""

from src.domain.connections import ConnectionStore
from src.domain.models import Connection


def execute(store: ConnectionStore, slug: str) -> Connection | None:
    return store.get(slug)
