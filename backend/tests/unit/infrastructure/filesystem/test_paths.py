from pathlib import Path

import pytest

from src.infrastructure.filesystem.paths import WorkspacePaths


@pytest.fixture
def paths(tmp_path: Path) -> WorkspacePaths:
    return WorkspacePaths(workspace_root=tmp_path / "Atelier")


def test_works_dir_under_root(paths: WorkspacePaths) -> None:
    assert paths.works_dir() == paths.workspace_root / "works"


def test_work_paths_compose(paths: WorkspacePaths) -> None:
    root = paths.workspace_root
    assert paths.work_dir("WRK-001") == root / "works" / "WRK-001"
    assert paths.work_json("WRK-001") == root / "works" / "WRK-001" / "work.json"
    assert paths.brief("WRK-001") == root / "works" / "WRK-001" / "brief.md"


def test_agent_paths_compose(paths: WorkspacePaths) -> None:
    root = paths.workspace_root
    expected_dir = root / "works" / "WRK-001" / "agents" / "developer-2"
    assert paths.agent_dir("WRK-001", "developer-2") == expected_dir
    assert paths.transcript("WRK-001", "developer-2") == expected_dir / "transcript.ndjson"


@pytest.mark.parametrize(
    "bad_slug",
    [
        "",
        "..",
        "../etc/passwd",
        "a/b",
        "a\\b",
        ".hidden",
        "with space",
        "with\nnewline",
        "with\x00null",
        "x" * 65,
    ],
)
def test_invalid_slug_rejected(paths: WorkspacePaths, bad_slug: str) -> None:
    with pytest.raises(ValueError, match="invalid slug"):
        paths.work_dir(bad_slug)


def test_invalid_agent_slug_rejected_even_when_work_slug_valid(
    paths: WorkspacePaths,
) -> None:
    with pytest.raises(ValueError, match="invalid slug"):
        paths.agent_dir("WRK-001", "..")


@pytest.mark.parametrize("good_slug", ["WRK-001", "agt-7", "con-3", "x", "a_b-c", "Agt7"])
def test_valid_slugs_accepted(paths: WorkspacePaths, good_slug: str) -> None:
    paths.work_dir(good_slug)  # does not raise


def test_constructed_paths_stay_under_root(paths: WorkspacePaths) -> None:
    """Defense in depth: even valid slugs produce paths under workspace_root."""
    p = paths.transcript("WRK-001", "developer-2")
    assert paths.workspace_root in p.parents
