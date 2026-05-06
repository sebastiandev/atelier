"""Singledispatch-routed fetchers for connection-backed contexts.

Adding a source = add a config dataclass (in ``domain/connections/configs.py``)
+ register a handler here. Network/HTTP/shape errors map to
``ContextFetchError`` so the caller has one exception type.

Registered today: ``jira``, ``sentry``. Honeycomb falls through to the
``@singledispatch`` default which raises a descriptive
``ContextFetchError`` rather than a ``NotImplementedError`` — the error
surfaces to the user as a 422 with an actionable message.
"""

from functools import singledispatch

from src.domain.connections.configs import ConnectionConfig, JiraConfig, SentryConfig
from src.domain.connections.dtos import ContextFetchError
from src.domain.models import Connection, Context
from src.infrastructure.connections.fetchers.jira import fetch_jira
from src.infrastructure.connections.fetchers.sentry import fetch_sentry


def fetch_context(connection: Connection, context: Context, token: str) -> str:
    """Public entry point; matches the ContextFetcher Protocol."""
    return _fetch(connection.config, context, token)


@singledispatch
def _fetch(config: ConnectionConfig, context: Context, token: str) -> str:
    raise ContextFetchError(
        f"context fetching not yet supported for config: {type(config).__name__}"
    )


@_fetch.register
def _(config: JiraConfig, context: Context, token: str) -> str:
    return fetch_jira(config, context, token)


@_fetch.register
def _(config: SentryConfig, context: Context, token: str) -> str:
    return fetch_sentry(config, context, token)


__all__ = ["fetch_context"]
