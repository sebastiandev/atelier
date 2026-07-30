"""Manifests written before the rename still open."""

import pytest

from src.domain.planning import manifest_keys


@pytest.mark.parametrize(
    ("manifest", "expected_dir", "expected_path"),
    [
        (
            {"plan_artifacts_dir": "bmad/x", "plan_artifacts_path": "/repo/bmad/x"},
            "bmad/x",
            "/repo/bmad/x",
        ),
        # Written before the rename.
        (
            {"artifact_root": "bmad/x", "artifact_root_path": "/repo/bmad/x"},
            "bmad/x",
            "/repo/bmad/x",
        ),
        # Half-migrated: a manifest touched by a new build keeps converging.
        (
            {"plan_artifacts_dir": "bmad/x", "artifact_root_path": "/repo/bmad/x"},
            "bmad/x",
            "/repo/bmad/x",
        ),
        # An empty new key must not shadow a populated legacy one.
        (
            {"plan_artifacts_dir": "", "artifact_root": "bmad/x"},
            "bmad/x",
            None,
        ),
        ({}, None, None),
        (None, None, None),
    ],
    ids=["current", "legacy", "half-migrated", "empty-new-key", "empty", "missing"],
)
def test_either_spelling_resolves(
    manifest: dict | None, expected_dir: str | None, expected_path: str | None
) -> None:
    assert manifest_keys.plan_artifacts_dir(manifest) == expected_dir
    assert manifest_keys.plan_artifacts_path(manifest) == expected_path
