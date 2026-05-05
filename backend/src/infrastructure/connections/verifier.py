"""HTTP verifier for ConnectionStore.

Calls the source's auth endpoint to confirm the supplied token works:

  - Jira:      ``GET {url}/rest/api/3/myself`` with Basic auth (email + token)
  - Sentry:    ``GET https://sentry.io/api/0/organizations/{org}/`` with Bearer token
  - Honeycomb: ``GET https://api.honeycomb.io/1/auth`` with X-Honeycomb-Team

Dispatch via ``functools.singledispatch`` on the typed config — adding a
source means adding a config dataclass + a ``@_verify.register`` handler.
Network errors map to ``VerifyResult(verified=False, error=...)`` — the
verifier never raises, so the calling service can persist the unverified
state without a try/except.
"""

from functools import singledispatch

import httpx

from src.domain.connections.configs import (
    ConnectionConfig,
    HoneycombConfig,
    JiraConfig,
    SentryConfig,
)
from src.domain.connections.dtos import VerifyResult
from src.domain.models import Connection

_TIMEOUT_SECONDS = 8.0


def verify(connection: Connection, token: str) -> VerifyResult:
    """Public entry point; matches the ConnectionVerifier Protocol."""
    try:
        return _verify(connection.config, token)
    except httpx.HTTPError as exc:
        return VerifyResult(verified=False, error=f"network error: {exc}")


@singledispatch
def _verify(config: ConnectionConfig, token: str) -> VerifyResult:
    return VerifyResult(verified=False, error=f"unsupported config: {type(config).__name__}")


@_verify.register
def _(config: JiraConfig, token: str) -> VerifyResult:
    base = config.url.rstrip("/")
    response = httpx.get(
        f"{base}/rest/api/3/myself",
        auth=(config.email, token),
        timeout=_TIMEOUT_SECONDS,
    )
    return _result_for(response)


@_verify.register
def _(config: SentryConfig, token: str) -> VerifyResult:
    response = httpx.get(
        f"https://sentry.io/api/0/organizations/{config.org}/",
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT_SECONDS,
    )
    return _result_for(response)


@_verify.register
def _(config: HoneycombConfig, token: str) -> VerifyResult:
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


__all__ = ["verify"]
