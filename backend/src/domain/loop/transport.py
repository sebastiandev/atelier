"""Portable import/export transport for reusable loops and stages.

A loop stored on disk is a sparse ``loop.yaml`` whose ``use:`` references
point at library stages the recipient may not own. To make a loop shareable
this module flattens the *resolved* definition into a self-contained
document: every linked stage is emitted with its full inline body **plus** a
``linked_from: <id>@<rev>`` marker (and the source stage's ``outcomes`` /
``description``) so the importer can rebuild the link against its own library.

``linked_from`` is transport-only. It is written by :func:`export_loop`,
read by :func:`parse_loop_import`, and never persisted — after import a
reconstituted stage is a plain ``use:`` reference again.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from src.domain.loop.dtos import (
    AgentStage,
    LoopDefinition,
    LoopOutcome,
    LoopPermission,
    LoopStepDefinition,
    StageDefinition,
    StageDefinitionRef,
    StageOverrides,
)
from src.domain.loop.snapshots import (
    loop_stage_from_document,
    loop_stage_from_snapshot,
    loop_stage_snapshot,
)

TRANSPORT_SCHEMA_VERSION = 1

_LOOP_KIND = "loop"
_STAGE_KIND = "stage"


class TransportInvalid(ValueError):
    """An uploaded import document is malformed or of the wrong kind."""


class StageImportStatus(StrEnum):
    """How one imported loop stage relates to the recipient's library."""

    INLINE = "inline"
    LINK_CLEAN = "link-clean"
    LINK_CONFLICT = "link-conflict"


@dataclass(frozen=True)
class ImportedStage:
    """One parsed loop stage, kept unresolved against the local library.

    For a linked stage ``stage`` is the fully-resolved (effective) body. Any
    loop-local overrides are recovered at import time by diffing it against the
    recipient's own base stage (:func:`overrides_from_drift`).
    """

    stage: LoopStepDefinition
    linked_from: StageDefinitionRef | None = None
    source_outcomes: tuple[LoopOutcome, ...] = ()
    source_description: str = ""
    source_forked_from: str | None = None


@dataclass(frozen=True)
class ImportedLoop:
    """A parsed loop transport document, before any local reconstitution."""

    name: str
    description: str
    source_id: str
    stages: tuple[ImportedStage, ...]


@dataclass(frozen=True)
class ImportedStandaloneStage:
    """A parsed standalone-stage transport document."""

    name: str
    description: str
    source_id: str
    outcomes: tuple[LoopOutcome, ...]
    stage: LoopStepDefinition
    forked_from: str | None = None


# ---------------------------------------------------------------------------
# Export (pure serialisers)
# ---------------------------------------------------------------------------


def export_loop(
    definition: LoopDefinition,
    linked_sources: Mapping[str, StageDefinition],
) -> dict[str, Any]:
    """Serialise a resolved loop into a portable transport document.

    Preconditions: ``definition`` is fully resolved — each linked stage
    carries ``stage_ref`` and its materialised inline body; ``linked_sources``
    maps each linked stage's ``stage_ref.definition_id`` to its current
    library/built-in :class:`StageDefinition`.
    Postconditions: returns a JSON/YAML-safe mapping. Linked stages gain
    ``linked_from``/``outcomes`` and drop ``stage_ref``/``overrides``;
    genuinely inline stages are emitted unchanged.
    """
    return {
        "schema_version": TRANSPORT_SCHEMA_VERSION,
        "kind": _LOOP_KIND,
        "id": definition.definition_id,
        "name": definition.name,
        "description": definition.description,
        "stages": [_export_stage(stage, linked_sources) for stage in definition.stages],
    }


def export_stage(definition: StageDefinition) -> dict[str, Any]:
    """Serialise one standalone stage into a portable transport document.

    Preconditions: ``definition`` is a validated standalone stage (no loop
    wiring, no ``use:`` reference). Postconditions: returns a self-contained
    JSON/YAML-safe mapping.
    """
    document: dict[str, Any] = {
        "schema_version": TRANSPORT_SCHEMA_VERSION,
        "kind": _STAGE_KIND,
        "id": definition.definition_id,
        "name": definition.name,
        "description": definition.description,
        "outcomes": [outcome.value for outcome in definition.outcomes],
        "stage": loop_stage_snapshot(definition.stage),
    }
    if definition.forked_from:
        document["forked_from"] = definition.forked_from
    return document


def _export_stage(
    stage: LoopStepDefinition,
    linked_sources: Mapping[str, StageDefinition],
) -> dict[str, Any]:
    body = loop_stage_snapshot(stage)
    stage_ref = body.pop("stage_ref", None)
    body.pop("overrides", None)
    if stage_ref is None:
        return body
    source_id = str(stage_ref["definition_id"])
    revision = str(stage_ref["revision"])
    source = linked_sources.get(source_id)
    outcomes = (
        [outcome.value for outcome in source.outcomes]
        if source is not None
        else sorted(outcome.value for outcome in stage.transitions)
    )
    # The body is the fully-resolved (effective) stage so the file works even
    # when hand-inspected; ``linked_from`` lets import rebuild the link, and any
    # loop-local overrides are recovered on import by diffing this body against
    # the recipient's own base stage (``overrides_from_drift``).
    ordered: dict[str, Any] = {
        "id": body.pop("id"),
        "linked_from": f"{source_id}@{revision}",
        "outcomes": outcomes,
    }
    if source is not None and source.description:
        ordered["description"] = source.description
    if source is not None and source.forked_from:
        ordered["forked_from"] = source.forked_from
    ordered.update(body)
    return ordered


# ---------------------------------------------------------------------------
# Import (pure parsers)
# ---------------------------------------------------------------------------


def parse_loop_import(document: object) -> ImportedLoop:
    """Parse a loop transport document without resolving links locally.

    Preconditions: ``document`` came from a loop export (``kind: loop``).
    Postconditions: linked stages keep their inline body and their
    ``linked_from`` provenance; no local repository is consulted.
    """
    data = _require_document(document, _LOOP_KIND)
    raw_stages = data.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise TransportInvalid("import document has no stages")
    return ImportedLoop(
        name=_required_str(data, "name"),
        description=_optional_str(data.get("description")),
        source_id=_optional_str(data.get("id")),
        stages=tuple(_parse_stage(item) for item in raw_stages),
    )


def parse_stage_import(document: object) -> ImportedStandaloneStage:
    """Parse a standalone-stage transport document (``kind: stage``)."""
    data = _require_document(document, _STAGE_KIND)
    body = data.get("stage")
    if not isinstance(body, dict):
        raise TransportInvalid("stage import document has no stage body")
    outcomes = data.get("outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        raise TransportInvalid("stage import document has no outcomes")
    return ImportedStandaloneStage(
        name=_required_str(data, "name"),
        description=_optional_str(data.get("description")),
        source_id=_optional_str(data.get("id")),
        outcomes=_parse_outcomes(outcomes),
        stage=loop_stage_from_snapshot(body),
        forked_from=_optional_str(data.get("forked_from")) or None,
    )


def _parse_stage(value: object) -> ImportedStage:
    if not isinstance(value, dict):
        raise TransportInvalid("each stage must be a mapping")
    body = {
        key: item
        for key, item in value.items()
        if key not in {"linked_from", "outcomes", "description", "forked_from"}
    }
    try:
        stage = loop_stage_from_document(body)
    except ValueError as exc:
        # The reader speaks `ValueError`; this layer's callers catch
        # `TransportInvalid`, and an untranslated one leaves the import route
        # with a 500 where it should answer 422 and say what is wrong.
        raise TransportInvalid(f"stage {_optional_str(value.get('id')) or '?'}: {exc}") from exc
    linked_raw = value.get("linked_from")
    if linked_raw is None:
        return ImportedStage(stage=stage)
    if not isinstance(linked_raw, str) or "@" not in linked_raw:
        raise TransportInvalid("linked_from must be '<id>@<revision>'")
    source_id, _, revision = linked_raw.partition("@")
    if not source_id.strip():
        raise TransportInvalid("linked_from must name a stage id")
    raw_outcomes = value.get("outcomes")
    outcomes = (
        _parse_outcomes(raw_outcomes)
        if isinstance(raw_outcomes, list)
        else tuple(stage.transitions)
    )
    return ImportedStage(
        stage=stage,
        linked_from=StageDefinitionRef(source_id.strip(), revision.strip()),
        source_outcomes=outcomes,
        source_description=_optional_str(value.get("description")),
        source_forked_from=_optional_str(value.get("forked_from")) or None,
    )


# ---------------------------------------------------------------------------
# Classification and safety surface (pure)
# ---------------------------------------------------------------------------


def classify_linked_stage(
    imported: ImportedStage,
    local: StageDefinition | None,
) -> StageImportStatus:
    """Decide how one imported stage relates to the local library.

    Preconditions: ``local`` is the recipient's current stage for the
    linked id, or ``None`` when absent. Postconditions: a stage with no
    ``linked_from`` is ``INLINE``; a link to a missing or identical-revision
    stage is ``LINK_CLEAN``; a link whose local revision differs is
    ``LINK_CONFLICT`` (the only status that needs an importer decision).
    """
    if imported.linked_from is None:
        return StageImportStatus.INLINE
    if local is None:
        return StageImportStatus.LINK_CLEAN
    if local.revision == imported.linked_from.revision:
        return StageImportStatus.LINK_CLEAN
    return StageImportStatus.LINK_CONFLICT


def overrides_from_drift(
    effective: LoopStepDefinition,
    base: LoopStepDefinition,
) -> StageOverrides | None:
    """Recover loop-local overrides by diffing an effective stage vs its base.

    Preconditions: ``effective`` is the imported, fully-resolved stage body and
    ``base`` is the recipient's matching base stage. Postconditions: returns the
    sparse fields that differ (the same set :func:`resolve_stage_link` layers),
    or ``None`` when the effective body already equals the base — so a linked
    stage that was renamed / re-instructed keeps those changes instead of
    reverting to base content on a same-revision link.
    """
    overrides = StageOverrides(
        name=effective.name if effective.name != base.name else None,
        instructions=_differing(effective, base, "instructions"),
        inputs=effective.inputs if effective.inputs != base.inputs else None,
        reports=effective.reports if effective.reports != base.reports else None,
        history=effective.history if effective.history != base.history else None,
        agent=_differing(effective, base, "agent"),
        retry=effective.retry if effective.retry != base.retry else None,
        check_adapter=_differing(effective, base, "check_adapter"),
        check_command=_differing(effective, base, "check_command"),
        note_required=_differing(effective, base, "note_required"),
        review_gate=_differing(effective, base, "review_gate"),
        pr_config=_differing(effective, base, "pr_config"),
    )
    changed = (
        overrides.name,
        overrides.instructions,
        overrides.inputs,
        overrides.reports,
        overrides.history,
        overrides.agent,
        overrides.retry,
        overrides.check_adapter,
        overrides.check_command,
        overrides.note_required,
        overrides.review_gate,
        overrides.pr_config,
    )
    return overrides if any(value is not None for value in changed) else None


def _differing(effective: LoopStepDefinition, base: LoopStepDefinition, field: str) -> Any:
    """Return an override's value when the two stages disagree on one field.

    Kind-specific fields exist on one type and not another, so a stage that
    cannot carry the field simply has nothing to differ about.
    """
    value = getattr(effective, field, None)
    return value if value != getattr(base, field, None) else None


def stage_command_prefixes(stage: LoopStepDefinition) -> tuple[str, ...]:
    """Return the auto-allowed shell command prefixes carried by a stage."""
    if not isinstance(stage, AgentStage) or stage.agent.approved_command_prefixes is None:
        return ()
    return tuple(stage.agent.approved_command_prefixes)


def stage_grants_write(stage: LoopStepDefinition) -> bool:
    """Return whether a stage grants filesystem write permission."""
    return isinstance(stage, AgentStage) and stage.agent.permissions == LoopPermission.WRITE


def reconstituted_stage_definition(
    imported: ImportedStage,
    definition_id: str,
) -> StageDefinition:
    """Build a standalone stage from an imported linked stage's inline body.

    Preconditions: ``imported.linked_from`` is set; ``definition_id`` is the
    target library id. Postconditions: returns an unsaved, unrevisioned
    :class:`StageDefinition` with loop wiring stripped.
    """
    return StageDefinition(
        definition_id=definition_id,
        name=imported.stage.name,
        description=imported.source_description,
        forked_from=imported.source_forked_from,
        outcomes=imported.source_outcomes,
        stage=replace(
            imported.stage,
            step_id=definition_id,
            transitions={},
            stage_ref=None,
            overrides=None,
        ),
    )


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _require_document(document: object, kind: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise TransportInvalid("import document must be a mapping")
    version = document.get("schema_version")
    if version != TRANSPORT_SCHEMA_VERSION:
        raise TransportInvalid(f"unsupported schema_version: {version!r}")
    found = document.get("kind")
    if found != kind:
        raise TransportInvalid(
            f"expected a {kind} document, got {found!r}"
        )
    return document


def _parse_outcomes(value: list[Any]) -> tuple[LoopOutcome, ...]:
    try:
        return tuple(LoopOutcome(str(item)) for item in value)
    except ValueError as exc:
        raise TransportInvalid(f"invalid outcome: {exc}") from exc


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TransportInvalid(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "TRANSPORT_SCHEMA_VERSION",
    "ImportedLoop",
    "ImportedStage",
    "ImportedStandaloneStage",
    "StageImportStatus",
    "TransportInvalid",
    "classify_linked_stage",
    "export_loop",
    "export_stage",
    "overrides_from_drift",
    "parse_loop_import",
    "parse_stage_import",
    "reconstituted_stage_definition",
    "stage_command_prefixes",
    "stage_grants_write",
]
