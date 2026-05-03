"""Request/response DTOs for the ConnectionStore port.

Inputs are frozen so commands can't mutate them post-dispatch. The token
is part of the *input* DTOs only — never on read DTOs and never on the
domain `Connection` entity, which mirrors the SQLite row exactly. The
secret lives in the OS keychain alone.
"""

from dataclasses import dataclass

from src.domain.models import ConnectionType


@dataclass(frozen=True)
class CreateConnectionRequest:
    type: ConnectionType
    name: str
    token: str
    url: str | None = None
    org: str | None = None
    region: str | None = None
    env: str | None = None
    team: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class UpdateConnectionRequest:
    """Partial update — fields left as ``None`` stay unchanged.

    Passing ``token`` rewrites the keychain entry; metadata fields update
    the SQLite row. ``verified`` is **not** updatable here — it's owned by
    the verify path so callers can't lie about it.
    """

    slug: str
    name: str | None = None
    token: str | None = None
    url: str | None = None
    org: str | None = None
    region: str | None = None
    env: str | None = None
    team: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class VerifyResult:
    verified: bool
    error: str | None = None
