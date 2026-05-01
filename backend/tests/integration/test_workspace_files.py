"""Integration tests for FsWorkspaceFiles against a real tmp workspace."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.filesystem import FsWorkspaceFiles, WorkspacePaths


@pytest.fixture
def files(tmp_path: Path) -> FsWorkspaceFiles:
    return FsWorkspaceFiles(WorkspacePaths(workspace_root=tmp_path / "Atelier"))


def test_ensure_work_dir_creates_the_path(files: FsWorkspaceFiles, tmp_path: Path) -> None:
    files.ensure_work_dir("WRK-001")
    assert (tmp_path / "Atelier" / "works" / "WRK-001").is_dir()


def test_write_and_read_work_json(files: FsWorkspaceFiles) -> None:
    payload = {"slug": "WRK-001", "name": "Migration", "contexts": []}
    files.write_work_json("WRK-001", payload)
    assert files.read_work_json("WRK-001") == payload


def test_read_work_json_returns_none_when_missing(files: FsWorkspaceFiles) -> None:
    assert files.read_work_json("WRK-404") is None


def test_read_work_json_returns_none_on_corrupt_file(
    files: FsWorkspaceFiles, tmp_path: Path
) -> None:
    files.ensure_work_dir("WRK-001")
    (tmp_path / "Atelier" / "works" / "WRK-001" / "work.json").write_text("{bad json")
    assert files.read_work_json("WRK-001") is None


def test_write_brief(files: FsWorkspaceFiles, tmp_path: Path) -> None:
    files.write_brief("WRK-001", "## brief\n\nbody")
    brief = (tmp_path / "Atelier" / "works" / "WRK-001" / "brief.md").read_text()
    assert brief == "## brief\n\nbody"


def test_ensure_agent_dir_creates_nested_structure(files: FsWorkspaceFiles, tmp_path: Path) -> None:
    files.ensure_agent_dir("WRK-001", "agt-7")
    assert (tmp_path / "Atelier" / "works" / "WRK-001" / "agents" / "agt-7").is_dir()


def test_write_and_read_agent_json(files: FsWorkspaceFiles) -> None:
    payload = {"slug": "agt-1", "persona": "architect"}
    files.write_agent_json("WRK-001", "agt-1", payload)
    assert files.read_agent_json("WRK-001", "agt-1") == payload


def test_read_agent_json_returns_none_when_missing(files: FsWorkspaceFiles) -> None:
    assert files.read_agent_json("WRK-001", "agt-404") is None


def test_write_handoff_doc_returns_path(files: FsWorkspaceFiles, tmp_path: Path) -> None:
    path_str = files.write_handoff_doc("WRK-001", "h1.md", "# handoff")
    expected = tmp_path / "Atelier" / "works" / "WRK-001" / "handoffs" / "h1.md"
    assert Path(path_str) == expected
    assert expected.read_text() == "# handoff"


def test_write_handoff_doc_rejects_bad_filename(files: FsWorkspaceFiles) -> None:
    with pytest.raises(ValueError):
        files.write_handoff_doc("WRK-001", "../escape.md", "x")
    with pytest.raises(ValueError):
        files.write_handoff_doc("WRK-001", ".hidden", "x")
    with pytest.raises(ValueError):
        files.write_handoff_doc("WRK-001", "", "x")


def test_list_work_slugs_returns_subdirs_only(files: FsWorkspaceFiles, tmp_path: Path) -> None:
    files.ensure_work_dir("WRK-001")
    files.ensure_work_dir("WRK-002")
    # Stray file at works/ — must be ignored.
    works = tmp_path / "Atelier" / "works"
    (works / "stray.txt").write_text("noise")
    # Hidden dir — must be ignored.
    (works / ".hidden").mkdir()

    assert files.list_work_slugs() == ["WRK-001", "WRK-002"]


def test_list_work_slugs_empty_when_no_works_dir(files: FsWorkspaceFiles) -> None:
    assert files.list_work_slugs() == []


def test_list_agent_slugs_filters_to_agents(files: FsWorkspaceFiles, tmp_path: Path) -> None:
    files.ensure_agent_dir("WRK-001", "agt-1")
    files.ensure_agent_dir("WRK-001", "agt-2")
    # Other dirs at the work level should not appear here.
    (tmp_path / "Atelier" / "works" / "WRK-001" / "artifacts").mkdir()
    (tmp_path / "Atelier" / "works" / "WRK-001" / "handoffs").mkdir()

    assert files.list_agent_slugs("WRK-001") == ["agt-1", "agt-2"]


def test_invalid_work_slug_rejected(files: FsWorkspaceFiles) -> None:
    with pytest.raises(ValueError):
        files.write_work_json("../escape", {})
    with pytest.raises(ValueError):
        files.read_work_json("..")


def test_atomic_write_leaves_no_tmp(files: FsWorkspaceFiles, tmp_path: Path) -> None:
    files.write_work_json("WRK-001", {"x": 1})
    work_dir = tmp_path / "Atelier" / "works" / "WRK-001"
    siblings = sorted(p.name for p in work_dir.iterdir())
    assert siblings == ["work.json"]
