"""The `atelier` command's decisions, exercised without touching a checkout.

Only the pure parts are worth testing here: which installs a pull implies,
what the user is told to restart, and how a browser is chosen. The IO around
them is `git pull` and `npm install`, which the shell already tests.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "atelier_cli",
    Path(__file__).resolve().parents[4] / "scripts" / "atelier.py",
)
assert _SPEC and _SPEC.loader
atelier = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(atelier)


def _labels(changed: list[str]) -> list[str]:
    return [label for label, _argv, _cwd in atelier.pending_dependency_jobs(changed)]


# -- what a pull implies --------------------------------------------------


@pytest.mark.parametrize(
    "changed, expected",
    [
        ([], []),
        (["README.md"], []),
        (["backend/uv.lock"], ["uv sync"]),
        (["backend/pyproject.toml"], ["uv sync"]),
        (
            ["backend/acp-runtime/package-lock.json"],
            ["npm install --prefix backend/acp-runtime"],
        ),
        (["frontend/package-lock.json"], ["npm install (frontend)"]),
    ],
)
def test_a_changed_lockfile_implies_its_install(changed, expected) -> None:
    assert _labels(changed) == expected


def test_one_pull_can_imply_every_install() -> None:
    """Today's ACP bump moved two of the three at once."""
    changed = [
        "backend/uv.lock",
        "backend/acp-runtime/package-lock.json",
        "frontend/package.json",
    ]
    assert _labels(changed) == [
        "uv sync",
        "npm install --prefix backend/acp-runtime",
        "npm install (frontend)",
    ]


def test_a_lockfile_elsewhere_is_not_ours() -> None:
    assert _labels(["docs/package-lock.json", "backend/acp-runtime/README.md"]) == []


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
