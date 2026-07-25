"""Tests for loop context resolution."""

from pathlib import Path

from src.domain.loop.dtos import (
    LoopContextKind,
    LoopContextReference,
    LoopContextResolutionRequest,
)
from src.infrastructure.loop_context_resolver import FilesystemLoopContextResolver


def test_inline_note_is_resolved_as_stage_context(tmp_path: Path) -> None:
    request = LoopContextResolutionRequest(
        root_path=tmp_path,
        work_slug="WRK-001",
        target_ref="",
        plan_index_ref="",
        dependencies=(),
        shared_context_refs=(),
        references=(
            LoopContextReference(LoopContextKind.NOTE, ref="Check keyboard order."),
        ),
    )

    resolution = FilesystemLoopContextResolver().resolve(request)

    assert resolution.entries == ("note: Check keyboard order.",)
