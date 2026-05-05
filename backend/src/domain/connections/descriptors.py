"""Per-type form descriptors for the frontend.

Mirrors the ``domain/agents/specs.py::ProviderDescriptor`` pattern: one
descriptor per ConnectionType, exposing only what the UI needs to render
the form (label, glyph, docs URL, type-specific config fields, and a
pair of capability flags).

What's NOT in the descriptor:
- ``name`` and ``token`` fields. Universal across every type — the FE
  renders them from a fixed template, not from per-type metadata.
- ``type``-discriminator inside the form. The FE picks the type first
  (chooser UI), then renders the matching descriptor's config fields.

Capability flags drive UI-side filtering:
- ``verifiable`` — shows the "Verify" button on the connection card.
- ``context_fetchable`` — gates the "use as agent context" picker so
  users don't pick a source whose fetcher would fail at agent start.

Adding a new connection type means adding a config dataclass + a
descriptor here + a verifier handler + (optionally) a fetcher handler —
the descriptor is the only place the FE needs to learn about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.models import ConnectionType


@dataclass(frozen=True, kw_only=True)
class ConnectionField:
    id: str
    label: str
    placeholder: str | None = None
    required: bool = False
    # Renders as a password input with a reveal toggle. We don't have
    # any secret fields *inside* the typed config today (the token
    # lives outside it), but keeping the flag means future per-type
    # secrets (e.g. Sentry's separate org-level token) can ride this
    # path without a schema change.
    secret: bool = False
    # Enum-style fields (preset choices). Empty/None for free-text.
    options: list[str] | None = None


@dataclass(frozen=True, kw_only=True)
class ConnectionDescriptor:
    type: ConnectionType
    label: str
    glyph: str
    docs: str
    config_fields: list[ConnectionField] = field(default_factory=list)
    verifiable: bool = True
    context_fetchable: bool = False


DESCRIPTORS: dict[ConnectionType, ConnectionDescriptor] = {
    "jira": ConnectionDescriptor(
        type="jira",
        label="Jira",
        glyph="JR",
        docs="Create a token at id.atlassian.com/manage-profile/security/api-tokens",
        config_fields=[
            ConnectionField(
                id="url",
                label="Site URL",
                placeholder="https://acme.atlassian.net",
                required=True,
            ),
            ConnectionField(
                id="email",
                label="Account email",
                placeholder="you@acme.com",
                required=True,
            ),
        ],
        verifiable=True,
        context_fetchable=True,
    ),
    "sentry": ConnectionDescriptor(
        type="sentry",
        label="Sentry",
        glyph="SE",
        docs="Generate an auth token at sentry.io › Settings › Auth Tokens",
        config_fields=[
            ConnectionField(
                id="org",
                label="Org slug",
                placeholder="acme",
                required=True,
            ),
        ],
        verifiable=True,
        context_fetchable=True,
    ),
    "honeycomb": ConnectionDescriptor(
        type="honeycomb",
        label="Honeycomb",
        glyph="HC",
        docs="Find an environment API key at ui.honeycomb.io › Environment Settings",
        config_fields=[
            ConnectionField(
                id="env",
                label="Environment",
                placeholder="prod",
                required=True,
            ),
            ConnectionField(
                id="team",
                label="Team",
                placeholder="acme",
            ),
        ],
        verifiable=True,
        context_fetchable=False,
    ),
}


__all__ = ["DESCRIPTORS", "ConnectionDescriptor", "ConnectionField"]
