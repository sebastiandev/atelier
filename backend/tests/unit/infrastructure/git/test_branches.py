import subprocess
from pathlib import Path

from src.infrastructure.git.branches import list_branches


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def test_list_branches_returns_local_heads(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "README.md").write_text("hello\n")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    _git("branch", "develop", cwd=repo)
    _git("branch", "feature/x", cwd=repo)

    branches = list_branches(repo)

    assert set(branches) == {"main", "develop", "feature/x"}


def test_list_branches_returns_empty_for_non_git_folder(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert list_branches(plain) == []


def test_list_branches_returns_empty_for_missing_path(tmp_path: Path) -> None:
    assert list_branches(tmp_path / "nope") == []


def test_list_branches_sorted_by_recency(tmp_path: Path) -> None:
    """Most recently committed branch should appear first so the user's
    likely target is at the top."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "README.md").write_text("a\n")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-q", "-m", "main commit", cwd=repo)
    _git("checkout", "-q", "-b", "feature-recent", cwd=repo)
    (repo / "README.md").write_text("b\n")
    _git("commit", "-aq", "-m", "newer commit", cwd=repo)

    branches = list_branches(repo)

    assert branches[0] == "feature-recent"
    assert "main" in branches
