"""HTTP verifier for ConnectionStore.

Calls the source's auth endpoint to confirm the supplied token works:

  - Jira:      ``GET {url}/rest/api/3/myself`` with Basic auth (email + token)
  - Sentry:    ``GET https://sentry.io/api/0/`` with Bearer token
  - Honeycomb: ``GET https://api.honeycomb.io/1/auth`` with X-Honeycomb-Team

The dispatch happens via a per-type lookup; adding a source means adding
one entry. Network errors map to ``VerifyResult(verified=False, error=...)``
— the verifier never raises, so the calling service can persist the
unverified state without a try/except.
"""

from collections.abc import Callable

import httpx

from src.domain.connections.dtos import VerifyResult
from src.domain.models import Connection, ConnectionType

_TIMEOUT_SECONDS = 8.0


def verify(connection: Connection, token: str) -> VerifyResult:
    """Public entry point; matches the ConnectionVerifier Protocol."""
    handler = _HANDLERS.get(connection.type)
    if handler is None:
        return VerifyResult(verified=False, error=f"unsupported type: {connection.type}")
    try:
        return handler(connection, token)
    except httpx.HTTPError as exc:
        return VerifyResult(verified=False, error=f"network error: {exc}")


def _verify_jira(connection: Connection, token: str) -> VerifyResult:
    if not connection.url:
        return VerifyResult(verified=False, error="url required for Jira")
    if not connection.email:
        return VerifyResult(verified=False, error="email required for Jira")
    base = connection.url.rstrip("/")
    response = httpx.get(
        f"{base}/rest/api/3/myself",
        auth=(connection.email, token),
        timeout=_TIMEOUT_SECONDS,
    )
    return _result_for(response)


def _verify_sentry(connection: Connection, token: str) -> VerifyResult:
    response = httpx.get(
        "https://sentry.io/api/0/",
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT_SECONDS,
    )
    return _result_for(response)


def _verify_honeycomb(connection: Connection, token: str) -> VerifyResult:
    response = httpx.get(
        "https://api.honeycomb.io/1/auth",
        headers={"X-Honeycomb-Team": token},
        timeout=_TIMEOUT_SECONDS,
    )
    return _result_for(response)


def _result_for(response: httpx.Response) -> VerifyResult:
    if response.is_success:
        return VerifyResult(verified=True)
    return VerifyResult(
        verified=False,
        error=f"HTTP {response.status_code}",
    )


_HANDLERS: dict[ConnectionType, Callable[[Connection, str], VerifyResult]] = {
    "jira": _verify_jira,
    "sentry": _verify_sentry,
    "honeycomb": _verify_honeycomb,
}


__all__ = ["verify"]
