"""Singledispatch on the config type. Anything without a registered
handler falls through to the default which raises a descriptive
ContextFetchError instead of a NotImplementedError."""

from datetime import UTC, datetime

import pytest

from src.domain.connections.configs import SentryConfig
from src.domain.connections.dtos import ContextFetchError
from src.domain.models import Connection, Context
from src.infrastructure.connections.fetchers import fetch_context


def test_unsupported_config_type_raises_with_clear_message() -> None:
    connection = Connection(
        type="sentry",
        name="X",
        created_at=datetime.now(UTC),
        config=SentryConfig(org="acme"),
    )
    ctx = Context(type="sentry", value="ABC-1", conn_id="con-1")
    with pytest.raises(ContextFetchError, match="not yet supported.*SentryConfig"):
        fetch_context(connection, ctx, "tok")
