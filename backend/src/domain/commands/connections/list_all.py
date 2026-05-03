"""List all Connections."""

from src.domain.connections import ConnectionStore
from src.domain.models import Connection


def execute(store: ConnectionStore) -> list[Connection]:
    return store.list_all()
