"""Tests for the persona/role → system_prompt rendering helper."""

import os
from pathlib import Path

from src.domain.agents import detect_shared_envs, render_system_prompt


def test_render_includes_persona_and_role() -> None:
    out = render_system_prompt("architect", "design the schema")
    assert "architect" in out
    assert "design the schema" in out


def test_render_is_deterministic() -> None:
    a = render_system_prompt("developer", "build the API")
    b = render_system_prompt("developer", "build the API")
    assert a == b


def test_render_includes_workdir_when_supplied() -> None:
    """The agent needs to know its working directory explicitly —
    without it, models routinely write files to $HOME and then call
    record_doc with a relative path that the tracker resolves
    elsewhere."""
    out = render_system_prompt(
        "developer", "build it", workdir=Path("/Users/seba/repos/atelier")
    )
    assert "Working directory: /Users/seba/repos/atelier" in out
    assert "relative to this directory" in out


def test_render_omits_workdir_block_when_unset() -> None:
    out = render_system_prompt("developer", "build it")
    assert "Working directory" not in out


def test_render_omits_shares_block_when_empty() -> None:
    """No shares = no Shared folders section. Keeps the prompt quiet
    for projects that don't use shared folders."""
    out = render_system_prompt("developer", "build it")
    assert "Shared folders" not in out


def test_render_includes_shares_block_with_names_and_mount_paths() -> None:
    """When shares are mounted into the worktree, surface them so the
    agent knows where to look + understands the last-writer-wins
    contract."""
    from src.domain.sharedfolders.dtos import ShareSummary

    out = render_system_prompt(
        "developer",
        "build it",
        workdir=Path("/tmp/wt"),
        shares=[
            ShareSummary(name="BMAD", mount_path="_bmad-output"),
            ShareSummary(name="Runbooks", mount_path="docs/runbooks"),
        ],
    )
    assert "Shared folders" in out
    assert '"BMAD" at ./_bmad-output/' in out
    assert '"Runbooks" at ./docs/runbooks/' in out
    assert "last writer wins" in out


def test_render_omits_shared_envs_block_when_empty() -> None:
    """No shared envs = no warning. Most worktrees on non-symlinked
    setups have nothing to flag — the block stays out of the prompt."""
    out = render_system_prompt("developer", "build it")
    assert "Shared dev environment" not in out


def test_render_includes_shared_envs_block_with_listed_paths() -> None:
    """When the caller passes a list of shared env paths, the block
    surfaces each one and warns about install races."""
    out = render_system_prompt(
        "developer",
        "build it",
        shared_envs=[".venv", "backend/.venv", "frontend/node_modules"],
    )
    assert "Shared dev environment" in out
    assert "- .venv" in out
    assert "- backend/.venv" in out
    assert "- frontend/node_modules" in out
    assert "concurrent installs race" in out


def test_detect_shared_envs_returns_empty_for_none() -> None:
    assert detect_shared_envs(None) == []


def test_detect_shared_envs_finds_top_level_and_monorepo_symlinks(
    tmp_path: Path,
) -> None:
    """The helper mirrors the worktree-manager's symlink walk —
    top-level first, then one level deep for monorepos."""
    # Sources to point the symlinks at; only their existence matters.
    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "venv").mkdir()
    (src_root / "node_modules").mkdir()
    (src_root / "backend").mkdir()
    (src_root / "backend" / ".venv").mkdir()
    (src_root / "frontend").mkdir()
    (src_root / "frontend" / "node_modules").mkdir()

    workdir = tmp_path / "wt"
    workdir.mkdir()
    os.symlink(src_root / "venv", workdir / "venv")
    os.symlink(src_root / "node_modules", workdir / "node_modules")
    (workdir / "backend").mkdir()
    os.symlink(src_root / "backend" / ".venv", workdir / "backend" / ".venv")
    (workdir / "frontend").mkdir()
    os.symlink(
        src_root / "frontend" / "node_modules",
        workdir / "frontend" / "node_modules",
    )
    # Plain directory (not a symlink) must NOT show up.
    (workdir / "src").mkdir()

    result = detect_shared_envs(workdir)
    assert "venv" in result
    assert "node_modules" in result
    assert "backend/.venv" in result
    assert "frontend/node_modules" in result
    assert "src" not in result
