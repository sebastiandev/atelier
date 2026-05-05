"""Per-type connection configs.

Each external system (Jira, Sentry, Honeycomb) owns the set of fields
it actually needs — instead of a wide ``Connection`` row with nullable
columns for every type's union of fields. The shared ``Connection``
entity carries the slug/name/created_at/verified/last_used universals
plus a typed ``config`` that singledispatch can route on.

Storage: ``ConnectionRepository`` serialises the config to a single
JSON column at the SA boundary (``configs_to_dict`` / ``dict_to_config``
in ``mapping.py``); the dataclasses themselves stay framework-free.

Adding a new connection type means adding a config dataclass here +
registering verifier/fetcher handlers — no schema migration needed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from src.domain.models import ConnectionType


@dataclass(frozen=True)
class JiraConfig:
    url: str
    email: str


@dataclass(frozen=True)
class SentryConfig:
    org: str


@dataclass(frozen=True)
class HoneycombConfig:
    env: str
    team: str | None = None


ConnectionConfig = JiraConfig | SentryConfig | HoneycombConfig


_BY_TYPE: dict[ConnectionType, type[JiraConfig | SentryConfig | HoneycombConfig]] = {
    "jira": JiraConfig,
    "sentry": SentryConfig,
    "honeycomb": HoneycombConfig,
}


def config_to_dict(config: ConnectionConfig) -> dict[str, object]:
    """Plain dict — straight into JSON. Frozen dataclasses make this
    losslessly recoverable via ``dict_to_config``."""
    return asdict(config)


def dict_to_config(connection_type: ConnectionType, data: dict[str, object]) -> ConnectionConfig:
    """Build the typed config back from JSON. Unknown ``connection_type``
    values raise — the caller (``ConnectionRepository``) trusts the row's
    type column and the configs_to_dict / dict_to_config pair is the only
    place this dispatch lives."""
    cls = _BY_TYPE.get(connection_type)
    if cls is None:
        raise ValueError(f"unsupported connection type: {connection_type}")
    return cls(**data)  # type: ignore[arg-type]


__all__ = [
    "ConnectionConfig",
    "HoneycombConfig",
    "JiraConfig",
    "SentryConfig",
    "config_to_dict",
    "dict_to_config",
]
