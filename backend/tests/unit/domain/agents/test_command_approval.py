"""Unit tests for the approved-command chain matcher.

Fixtures drawn from real permission_request transcripts (opencode, review
stage): a chained ``git diff`` line, piped ``rg``/``grep``, a ``for`` loop,
and a bare ``wc``. See also the adversarial cases (substitution, redirection,
background jobs) a permission-matching function must refuse rather than
guess on.
"""

from __future__ import annotations

import pytest

from src.domain.agents.command_approval import (
    command_is_fully_approved,
    parse_command_prefix_tokens,
)

_PREFIXES = ("dt pytest", "dt sh", "git diff", "source .venv/bin/activate")


@pytest.mark.parametrize(
    "command",
    [
        "git diff",
        "git diff --stat",
        "git diff --stat && git diff --numstat",
        "git diff; git diff --stat",
        "git diff --stat 2>/dev/null",
        "git diff --stat 1>/dev/null 2>/dev/null",
        "dt sh -s app-endpoints && dt pytest -k foo",
        "source .venv/bin/activate && dt pytest",
    ],
)
def test_fully_covered_chains_are_approved(command: str) -> None:
    assert command_is_fully_approved(command, _PREFIXES) is True


@pytest.mark.parametrize(
    "command",
    [
        # The exact chained line from the transcript: git diff is approved,
        # but `echo` and `git status` are not — the whole line must still ask.
        'git diff --stat && echo "---UNTRACKED---" && git status --porcelain',
        # Piped into an unapproved command.
        'rg -n "a|b|c" file.py | head -60',
        # Semicolon-chained into unapproved commands.
        'sed -n 1,76p file.py; echo "==="; grep -n "x" .gitignore',
        # A bare command whose tool simply isn't approved.
        "wc -l a.py b.py",
        # A real control-structure loop — never decomposable into simple commands.
        'git diff --numstat; echo "---"; for f in a.py b.py; do echo "$f"; done',
    ],
)
def test_uncovered_or_undecomposable_chains_ask(command: str) -> None:
    assert command_is_fully_approved(command, _PREFIXES) is False


@pytest.mark.parametrize(
    "command",
    [
        "",
        "   ",
        "git diff && ",  # trailing empty simple command
        "&& git diff",  # leading empty simple command
        "git diff &",  # background job
        "sleep 5 & git diff",
        "git diff > out.txt",  # real (non-discard) redirection
        "git diff >> out.txt",
        "git diff < input.txt",
        "echo $(git diff)",  # command substitution
        "echo `git diff`",  # backtick substitution
        "( git diff )",  # subshell / grouping
        "{ git diff; }",  # brace group
        "for f in a b; do git diff; done",  # control structure
        "if git diff; then echo ok; fi",
        'git diff "unterminated',  # malformed quoting → shlex raises
    ],
)
def test_adversarial_commands_are_refused(command: str) -> None:
    assert command_is_fully_approved(command, _PREFIXES) is False


def test_no_approved_prefixes_never_approves() -> None:
    assert command_is_fully_approved("git diff", ()) is False


def test_prefix_must_match_from_the_start_not_a_substring() -> None:
    # "my-git diff" must not match the "git diff" prefix.
    assert command_is_fully_approved("my-git diff", ("git diff",)) is False


def test_parse_command_prefix_tokens_drops_invalid_entries() -> None:
    parsed = parse_command_prefix_tokens(
        ("git diff", "", "   ", "a\nb", "a && b", "dt sh")
    )
    assert parsed == (("git", "diff"), ("dt", "sh"))
