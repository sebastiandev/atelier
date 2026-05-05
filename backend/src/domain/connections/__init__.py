"""ConnectionStore — metadata in SQLite, tokens in the OS keychain."""

from src.domain.connections.configs import (
    ConnectionConfig,
    HoneycombConfig,
    JiraConfig,
    SentryConfig,
    config_to_dict,
    dict_to_config,
)
from src.domain.connections.descriptors import (
    DESCRIPTORS,
    ConnectionDescriptor,
    ConnectionField,
)
from src.domain.connections.dtos import (
    ContextFetchError,
    CreateConnectionRequest,
    UpdateConnectionRequest,
    VerifyResult,
)
from src.domain.connections.ports import (
    ConnectionRepository,
    ConnectionStore,
    ConnectionVerifier,
    ContextFetcher,
    SecretStore,
)
from src.domain.connections.service import ConnectionStoreService

__all__ = [
    "DESCRIPTORS",
    "ConnectionConfig",
    "ConnectionDescriptor",
    "ConnectionField",
    "ConnectionRepository",
    "ConnectionStore",
    "ConnectionStoreService",
    "ConnectionVerifier",
    "ContextFetchError",
    "ContextFetcher",
    "CreateConnectionRequest",
    "HoneycombConfig",
    "JiraConfig",
    "SecretStore",
    "SentryConfig",
    "UpdateConnectionRequest",
    "VerifyResult",
    "config_to_dict",
    "dict_to_config",
]
