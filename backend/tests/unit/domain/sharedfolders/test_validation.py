"""Mount-path validation rules.

These match the constraints documented in STORY-032 and enforced
again at the HTTP boundary (400 on rejection). Keeping the unit tests
narrow lets us regress-test individual rules without spinning up the
full route stack.
"""

from __future__ import annotations

import pytest

from src.domain.sharedfolders import InvalidMountPath, validate_mount_path


def test_accepts_simple_relative_path() -> None:
    assert validate_mount_path("_bmad-output") == "_bmad-output"


def test_accepts_nested_relative_path() -> None:
    assert validate_mount_path("docs/runbooks") == "docs/runbooks"


def test_strips_trailing_slash() -> None:
    # Stored canonical form has no trailing slash; we normalise on write.
    assert validate_mount_path("_bmad-output/") == "_bmad-output"


def test_strips_leading_dot_segment() -> None:
    assert validate_mount_path("./notes") == "notes"


def test_rejects_empty() -> None:
    with pytest.raises(InvalidMountPath):
        validate_mount_path("")


def test_rejects_whitespace_only() -> None:
    with pytest.raises(InvalidMountPath):
        validate_mount_path("   ")


def test_rejects_absolute_path() -> None:
    with pytest.raises(InvalidMountPath, match="relative"):
        validate_mount_path("/etc/passwd")


def test_rejects_parent_traversal() -> None:
    with pytest.raises(InvalidMountPath, match=r"\.\."):
        validate_mount_path("docs/../secrets")


def test_rejects_traversal_at_start() -> None:
    with pytest.raises(InvalidMountPath, match=r"\.\."):
        validate_mount_path("../outside")


def test_rejects_backslashes() -> None:
    """Backslashes break os.symlink targets and confuse shell tools the
    agent will invoke. POSIX-style separators only."""
    with pytest.raises(InvalidMountPath, match="'\\\\'"):
        validate_mount_path("docs\\runbooks")


def test_rejects_null_byte() -> None:
    with pytest.raises(InvalidMountPath, match="null byte"):
        validate_mount_path("docs/\x00bad")


def test_rejects_overlong_segment() -> None:
    long = "a" * 65
    with pytest.raises(InvalidMountPath, match="too long"):
        validate_mount_path(long)
