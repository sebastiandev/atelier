"""Request/response DTOs for the ConnectionStore port.

Inputs are frozen so commands can't mutate them post-dispatch. The token
is part of the *input* DTOs only — never on read DTOs and never on the
domain `Connection` entity, which mirrors the SQLite row exactly. The
secret lives in the OS keychain alone.
"""

from dataclasses import dataclass

from src.domain.connections.configs import ConnectionConfig
from src.domain.models import ConnectionType


@dataclass(frozen=True)
class CreateConnectionRequest:
    type: ConnectionType
    name: str
    token: str
    config: ConnectionConfig


@dataclass(frozen=True)
class UpdateConnectionRequest:
    """Partial update — ``None`` fields stay unchanged.

    Passing ``token`` rewrites the keychain entry. Passing ``config``
    replaces the typed config wholesale (it's a single value; partial
    updates of inner fields would mean introducing per-type partial DTOs
    for marginal gain). ``verified`` is owned by the verify path."""

    slug: str
    name: str | None = None
    token: str | None = None
    config: ConnectionConfig | None = None


@dataclass(frozen=True)
class VerifyResult:
    verified: bool
    error: str | None = None


class ContextFetchError(Exception):
    """A connection-backed context could not be fetched.

    Raised at agent-start time when a fetcher rejects the request (auth,
    network, missing resource) or when the connection is missing / has
    no token in the keychain. The route maps this to 422 — the user
    picked a context they can't access, so the agent shouldn't start.
    """
