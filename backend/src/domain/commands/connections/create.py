"""Create a new Connection."""

from src.domain.connections import ConnectionStore, CreateConnectionRequest
from src.domain.models import Connection


def execute(store: ConnectionStore, req: CreateConnectionRequest) -> Connection:
    return store.create(req)
