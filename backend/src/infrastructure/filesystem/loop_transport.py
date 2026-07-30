"""YAML codec for the portable loop/stage import-export transport.

The domain owns the transport *shape* (a plain mapping); this adapter is the
only place that turns that mapping into YAML bytes and back, keeping the
YAML dependency out of the domain and application layers.
"""

from __future__ import annotations

from typing import Any

import yaml  # type: ignore[import-untyped]

from src.domain.loop.transport import TransportInvalid


def dump_transport_document(document: dict[str, Any]) -> str:
    """Serialise a transport mapping to a stable, human-readable YAML string."""
    dumped: str = yaml.safe_dump(document, sort_keys=False, allow_unicode=False)
    return dumped


def parse_transport_document(text: str) -> dict[str, Any]:
    """Parse uploaded YAML into a mapping, rejecting anything else.

    Preconditions: ``text`` is the raw contents of an uploaded export file.
    Postconditions: returns a mapping; raises :class:`TransportInvalid` for
    malformed YAML or a non-mapping top level.
    """
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TransportInvalid(f"import file is not valid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise TransportInvalid("import file must contain a mapping")
    return loaded


__all__ = ["dump_transport_document", "parse_transport_document"]
