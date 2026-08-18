"""A git subprocess must never be able to ask a question.

The prompt would land on the server's terminal rather than in front of
the user, and the caller would wait on an answer that never comes.
"""

from __future__ import annotations

import pytest

from src.infrastructure.git.env import auth_hint, git_env


def test_ssh_runs_in_batch_mode_so_a_passphrase_fails_instead_of_prompting() -> None:
    assert "-o BatchMode=yes" in git_env()["GIT_SSH_COMMAND"]


def test_a_configured_ssh_command_is_extended_not_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /keys/deploy -p 2222")

    command = git_env()["GIT_SSH_COMMAND"]

    assert command == "ssh -i /keys/deploy -p 2222 -o BatchMode=yes"


@pytest.mark.parametrize(
    "variable, value",
    [
        ("GIT_TERMINAL_PROMPT", "0"),
        ("GIT_ASKPASS", ""),
        ("SSH_ASKPASS", ""),
        ("SSH_ASKPASS_REQUIRE", "never"),
        ("GCM_INTERACTIVE", "never"),
    ],
)
def test_interactive_credential_paths_are_disabled(variable: str, value: str) -> None:
    assert git_env()[variable] == value


def test_an_inherited_askpass_helper_cannot_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GUI askpass would open a dialog on the host running the server."""
    monkeypatch.setenv("SSH_ASKPASS", "/usr/bin/ssh-askpass")

    assert git_env()["SSH_ASKPASS"] == ""


def test_the_rest_of_the_environment_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATELIER_UNRELATED", "kept")

    assert git_env()["ATELIER_UNRELATED"] == "kept"


@pytest.mark.parametrize(
    "stderr",
    [
        "git@github.com: Permission denied (publickey).",
        "Host key verification failed.",
        "fatal: could not read from remote repository.",
        "remote: Authentication failed for 'https://example.com/repo'",
        "could not read Username: terminal prompts disabled",
        "no such identity: /home/u/.ssh/id_ed25519: No such file or directory",
    ],
)
def test_a_credential_failure_says_how_to_fix_it(stderr: str) -> None:
    assert "ssh-add" in auth_hint(stderr)


@pytest.mark.parametrize(
    "stderr",
    [
        "fatal: invalid reference: nope",
        "fatal: 'main' is already checked out at '/tmp/wt'",
        "",
    ],
)
def test_an_unrelated_failure_gets_no_credential_advice(stderr: str) -> None:
    assert auth_hint(stderr) == ""
