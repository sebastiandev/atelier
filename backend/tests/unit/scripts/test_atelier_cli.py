"""The `atelier` command's decisions, exercised without touching a checkout.

Only the pure parts are worth testing here: which installs a pull implies,
what the user is told to restart, and how a browser is chosen. The IO around
them is `git pull` and `npm install`, which the shell already tests.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "atelier_cli",
    Path(__file__).resolve().parents[4] / "scripts" / "atelier.py",
)
assert _SPEC and _SPEC.loader
atelier = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(atelier)


def _labels(jobs) -> list[str]:
    return [label for label, _argv, _cwd in jobs]


# -- deciding what to install ---------------------------------------------
#
# The rule under test: staleness is a property of the working tree, not of
# the commit range a pull happened to move. Asking the range has a wrong
# answer whenever the lockfile changed outside it -- a manual `git pull`
# first, or a later pull that carries no lockfile -- and the consequence is
# new code running against old dependencies.


def _tree(tmp_path, *, lock_mtime: float | None, marker_mtime: float | None) -> None:
    """Lay out one lockfile/marker pair at the paths the CLI expects."""
    lock = tmp_path / "frontend" / "package-lock.json"
    marker = tmp_path / "frontend" / "node_modules" / ".package-lock.json"
    if lock_mtime is not None:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("{}")
        os.utime(lock, (lock_mtime, lock_mtime))
    if marker_mtime is not None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}")
        os.utime(marker, (marker_mtime, marker_mtime))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(atelier, "REPO", tmp_path)
    return tmp_path


def test_a_lockfile_newer_than_the_install_is_stale(repo) -> None:
    _tree(repo, lock_mtime=2000, marker_mtime=1000)
    assert atelier.is_stale(
        "frontend/package-lock.json", "frontend/node_modules/.package-lock.json"
    )


def test_an_install_newer_than_its_lockfile_is_current(repo) -> None:
    _tree(repo, lock_mtime=1000, marker_mtime=2000)
    assert not atelier.is_stale(
        "frontend/package-lock.json", "frontend/node_modules/.package-lock.json"
    )


def test_a_checkout_that_never_installed_is_stale(repo) -> None:
    """The two-month-old checkout, and the fresh clone, look the same here."""
    _tree(repo, lock_mtime=1000, marker_mtime=None)
    assert atelier.is_stale(
        "frontend/package-lock.json", "frontend/node_modules/.package-lock.json"
    )


def test_nothing_to_install_from_is_not_stale(repo) -> None:
    _tree(repo, lock_mtime=None, marker_mtime=None)
    assert not atelier.is_stale(
        "frontend/package-lock.json", "frontend/node_modules/.package-lock.json"
    )


def test_a_job_with_no_marker_always_runs(repo) -> None:
    """uv sync is its own staleness check, so it is never skipped."""
    assert atelier.is_stale("", "")


def test_the_manual_pull_still_installs(repo) -> None:
    """The regression this replaced.

    A user pulls by hand, then runs the command. The pull moves nothing and
    the range is empty -- but the tree is stale, so the installs must run.
    Under the old range-based rule this returned nothing at all.
    """
    _tree(repo, lock_mtime=2000, marker_mtime=1000)
    assert "npm install (frontend)" in _labels(atelier.pending_dependency_jobs())


def test_a_later_pull_that_carries_no_lockfile_still_installs(repo) -> None:
    """The half-fix the review caught.

    Hand-pull ten commits, then let the command pull one more that touches
    no lockfile. The range is non-empty and lockfile-free, so a range-based
    rule skips the install even though the tree is stale.
    """
    _tree(repo, lock_mtime=2000, marker_mtime=1000)
    assert "npm install (frontend)" in _labels(atelier.pending_dependency_jobs())


def test_a_current_tree_installs_nothing(repo) -> None:
    """The common case stays cheap: no unconditional network cost."""
    for lock, marker in (
        ("backend/acp-runtime/package-lock.json",
         "backend/acp-runtime/node_modules/.package-lock.json"),
        ("frontend/package-lock.json", "frontend/node_modules/.package-lock.json"),
    ):
        (repo / lock).parent.mkdir(parents=True, exist_ok=True)
        (repo / lock).write_text("{}")
        os.utime(repo / lock, (1000, 1000))
        (repo / marker).parent.mkdir(parents=True, exist_ok=True)
        (repo / marker).write_text("{}")
        os.utime(repo / marker, (2000, 2000))
    # uv sync has no marker and is always offered; the npm jobs must not be.
    assert _labels(atelier.pending_dependency_jobs()) == ["uv sync"]


# -- did an installer actually do anything? ------------------------------


@pytest.mark.parametrize(
    "output",
    [
        "Installed 10 packages in 19ms",
        "Uninstalled 3 packages",
        "added 12 packages, and audited 300 packages",
        "removed 4 packages",
    ],
)
def test_installer_work_is_recognised(output: str) -> None:
    assert atelier.changed_anything(output)


@pytest.mark.parametrize(
    "output",
    [
        "Resolved 67 packages in 3ms\nChecked 51 packages in 1ms",
        "",
        "up to date, audited 300 packages",
    ],
)
def test_a_no_op_install_is_not_reported_as_a_restart_reason(output: str) -> None:
    """`uv sync` runs on every update; calling that a change would put a
    'restart the backend' warning on every single run until it stopped
    being read."""
    assert not atelier.changed_anything(output)


# -- what the user must restart -------------------------------------------


def test_nothing_moved_means_nothing_to_restart() -> None:
    assert atelier.restart_notices([], []) == []


def test_a_touched_migrations_module_asks_for_a_backend_restart() -> None:
    notices = atelier.restart_notices(
        ["backend/src/infrastructure/database/migrations.py"], []
    )
    assert any("restart the backend" in n and "DB migrations" in n for n in notices)


def test_backend_dependencies_ask_for_a_backend_restart() -> None:
    notices = atelier.restart_notices(["backend/uv.lock"], ["uv sync"])
    assert any("restart the backend" in n for n in notices)


def test_new_acp_wrappers_ask_for_a_session_restart() -> None:
    """A running wrapper subprocess keeps the version it was spawned with."""
    notices = atelier.restart_notices(
        ["backend/acp-runtime/package-lock.json"],
        ["npm install --prefix backend/acp-runtime"],
    )
    assert any("agent and chat sessions" in n for n in notices)


@pytest.mark.parametrize(
    "path", ["frontend/src/AgentTile.tsx", "frontend/index.html", "frontend/vite.config.ts"]
)
def test_frontend_changes_ask_for_a_refresh(path: str) -> None:
    assert any("refresh the browser" in n for n in atelier.restart_notices([path], []))


def test_restart_advice_does_not_duplicate_when_several_reasons_apply() -> None:
    notices = atelier.restart_notices(
        ["backend/uv.lock", "backend/src/infrastructure/database/migrations.py"],
        ["uv sync"],
    )
    assert len([n for n in notices if "restart the backend" in n]) == 1


# -- choosing a browser ---------------------------------------------------


def test_prefers_the_first_installed_chromeless_browser() -> None:
    found = atelier.find_app_browsers(
        "Darwin", exists=lambda p: "Vivaldi" in p or "Chrome" in p
    )
    assert "Google Chrome" in found[0]


def test_reports_the_alternatives_rather_than_asking() -> None:
    """`launch` runs many times a day; a question with one answer is a tax."""
    notes = atelier.browser_report(["/x/Google Chrome", "/x/Vivaldi"])
    assert len(notes) == 1
    assert "Vivaldi" in notes[0] and "ATELIER_BROWSER" in notes[0]


def test_a_single_browser_needs_no_commentary() -> None:
    assert atelier.browser_report(["/x/Google Chrome"]) == []


def test_no_chromeless_browser_points_at_the_override() -> None:
    assert atelier.find_app_browsers("Darwin", exists=lambda p: False) == []
    notes = atelier.browser_report([])
    assert "ATELIER_BROWSER" in notes[0]


def test_the_override_wins_over_everything_installed() -> None:
    found = atelier.find_app_browsers(
        "Darwin", exists=lambda p: True, override="/opt/my-browser"
    )
    assert found == ["/opt/my-browser"]


def test_an_override_that_is_not_there_finds_nothing() -> None:
    found = atelier.find_app_browsers(
        "Linux", which=lambda n: None, exists=lambda p: False, override="/nope"
    )
    assert found == []


def test_linux_and_windows_look_up_binaries_by_name() -> None:
    assert atelier.find_app_browsers(
        "Linux", which=lambda n: f"/usr/bin/{n}" if n == "chromium" else None
    ) == ["/usr/bin/chromium"]
    assert atelier.find_app_browsers(
        "Windows", which=lambda n: rf"C:\{n}.exe" if n == "msedge" else None
    ) == [r"C:\msedge.exe"]


# -- waiting for the servers ----------------------------------------------


def test_stops_waiting_the_moment_the_servers_die() -> None:
    """A dead child means no window: an error page would blame the browser."""
    assert atelier.wait_for_port("127.0.0.1", 9, deadline=1e9, alive=lambda: False) is False


# -- the spinner ----------------------------------------------------------


def test_the_spinner_is_inert_when_nothing_is_watching(capsys) -> None:
    """Logs and CI get one clean line per step, not a smear of \r."""
    with atelier.Working("uv sync"):
        pass
    assert capsys.readouterr().out == ""


def test_the_spinner_stops_when_its_step_finishes() -> None:
    work = atelier.Working("uv sync", interval=0.01)
    with work:
        pass
    assert work._stop.is_set()
    assert work._thread is None or not work._thread.is_alive()
