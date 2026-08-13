"""Tests for where a materializer says it wrote its plan."""

import pytest

from src.domain.planning.materialization import (
    InvalidMaterializationReport,
    _entries_from_report,
)

_PLANNING_FOLDER = "/repo/bmad"


def _report(root: object = None) -> dict[str, object]:
    """Return a report whose paths are relative to whatever it names."""
    artifacts = [
        {
            "path": "brief.md",
            "title": "Brief",
            "artifact_kind": "brief",
            "executable": False,
            "dependencies": [],
        },
        {
            "path": "stories/01-first.md",
            "title": "First story",
            "artifact_kind": "story",
            "executable": True,
            "dependencies": ["brief.md"],
        },
    ]
    report: dict[str, object] = {"artifacts": artifacts}
    if root is not None:
        report["root"] = root
    return report


def test_a_report_without_a_root_is_relative_to_the_planning_folder() -> None:
    entries = _entries_from_report(_report(), _PLANNING_FOLDER)

    assert [e.path for e in entries] == ["brief.md", "stories/01-first.md"]


def test_a_reported_folder_prefixes_paths_and_dependencies() -> None:
    # Grouping a plan in its own folder is the right call in a repository that
    # holds several; saying so once is all it takes for the paths to resolve.
    entries = _entries_from_report(_report("app-metrics"), _PLANNING_FOLDER)

    assert [e.path for e in entries] == ["app-metrics/brief.md", "app-metrics/stories/01-first.md"]
    assert entries[1].dependencies == ("app-metrics/brief.md",)


def test_an_absolute_folder_inside_the_planning_folder_is_accepted() -> None:
    entries = _entries_from_report(_report("/repo/bmad/app-metrics"), _PLANNING_FOLDER)

    assert entries[0].path == "app-metrics/brief.md"


def test_naming_the_planning_folder_itself_changes_nothing() -> None:
    entries = _entries_from_report(_report("/repo/bmad"), _PLANNING_FOLDER)

    assert entries[0].path == "brief.md"


@pytest.mark.parametrize(
    "root",
    ["/somewhere/else", "../outside", "app-metrics/../../escape"],
)
def test_a_folder_outside_the_planning_folder_is_refused(root: str) -> None:
    with pytest.raises(InvalidMaterializationReport):
        _entries_from_report(_report(root), _PLANNING_FOLDER)
