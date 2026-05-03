"""Update Connection metadata or rotate its token."""

from src.domain.connections import ConnectionStore, UpdateConnectionRequest
from src.domain.models import Connection


def execute(store: ConnectionStore, req: UpdateConnectionRequest) -> Connection:
    return store.update(req)
