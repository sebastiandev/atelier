"""The manifest records run ids; SQL owns run state."""

from src.domain.planning.actions import record_artifact_run_id


def test_records_a_run_id() -> None:
    manifest: dict = {}

    record_artifact_run_id(manifest, "story-001", "run-001")

    assert manifest["artifact_runs"] == {"story-001": ["run-001"]}


def test_is_idempotent() -> None:
    manifest: dict = {"artifact_runs": {"story-001": ["run-001"]}}

    record_artifact_run_id(manifest, "story-001", "run-001")

    assert manifest["artifact_runs"]["story-001"] == ["run-001"]


def test_normalises_manifests_that_still_hold_run_bodies() -> None:
    """Pre-existing manifests must not end up a mix of dicts and strings."""
    manifest: dict = {
        "artifact_runs": {
            "story-001": [{"id": "run-001", "loop": {"status": "accepted"}}]
        }
    }

    record_artifact_run_id(manifest, "story-001", "run-002")

    assert manifest["artifact_runs"]["story-001"] == ["run-001", "run-002"]


def test_drops_an_unusable_legacy_row() -> None:
    manifest: dict = {"artifact_runs": {"story-001": [{"loop": {}}]}}

    record_artifact_run_id(manifest, "story-001", "run-001")

    assert manifest["artifact_runs"]["story-001"] == ["run-001"]
