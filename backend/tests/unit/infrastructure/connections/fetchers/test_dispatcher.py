"""Singledispatch on the config type. Anything without a registered
handler falls through to the default which raises a descriptive
ContextFetchError instead of a NotImplementedError."""

from datetime import UTC, datetime

import pytest

from src.domain.connections.configs import HoneycombConfig
from src.domain.connections.dtos import ContextFetchError
from src.domain.models import Connection, Context
from src.infrastructure.connections.fetchers import fetch_context


def test_unsupported_config_type_raises_with_clear_message() -> None:
    connection = Connection(
        type="honeycomb",
        name="X",
        created_at=datetime.now(UTC),
        config=HoneycombConfig(env="prod"),
    )
    ctx = Context(type="honeycomb", value="anything", conn_id="con-1")
    with pytest.raises(ContextFetchError, match="not yet supported.*HoneycombConfig"):
        fetch_context(connection, ctx, "tok")
