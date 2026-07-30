"""Decide whether an agent's requested shell command is already approved.

An agent stage's ``approved_command_prefixes`` names atomic commands whose
provider-native permission policy is pre-configured to allow them (see
``infrastructure/agents/acp/providers.py``). A provider's own permission
engine only ever recognises a *single* command by its prefix, so a request
that chains an approved command with something else (``git diff && rm -rf
/``, ``git diff | wc -l``) still falls through to asking a human — even
though, say, the ``git diff`` half is individually approved.

``command_is_fully_approved`` closes that gap for the common case of a
plain shell chain (``&&``, ``||``, ``;``, ``|``) whose every simple command
matches an approved prefix. It is deliberately conservative: any construct
it cannot confidently reason about (command substitution, redirection other
than discarding to ``/dev/null``, a background job, or a control-structure
keyword such as ``for``/``if``/``case``) refuses rather than guesses, so the
caller falls back to asking. A refusal here is always safe; a wrong
approval is not.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence

_SEPARATORS = frozenset({"&&", "||", ";", "|"})
_OPERATOR_CHARS = frozenset(";&|")
_SAFE_DISCARD_REDIRECT = re.compile(r"^([012]?>>?|&>>?)/dev/null$")
_CONTROL_KEYWORDS = re.compile(
    r"\b(?:for|while|until|if|then|elif|else|fi|do|done|case|esac|function|select)\b"
)
# Constructs we refuse to reason about at all, checked on the raw text:
# command substitution (`` ` `` / ``$(``) and any grouping punctuation that
# could hide a subshell or brace-group compound command.
_UNSAFE_LITERAL_MARKERS = ("`", "$(", "(", ")", "{", "}")


def command_is_fully_approved(
    command: str, approved_prefixes: Sequence[str]
) -> bool:
    """Return whether every simple command in ``command`` matches an approved prefix.

    Preconditions: ``command`` is the literal shell string an agent asked to
    run; ``approved_prefixes`` is the stage's resolved allowlist.
    Postconditions: returns ``False`` (defer to asking) for anything this
    cannot confidently decompose — it never returns ``True`` for a command
    it has not fully accounted for.
    """
    if not command.strip() or not approved_prefixes:
        return False
    if any(marker in command for marker in _UNSAFE_LITERAL_MARKERS):
        return False
    if _CONTROL_KEYWORDS.search(command):
        return False

    tokens = _tokenize(command)
    if not tokens:
        return False

    prefix_token_lists = parse_command_prefix_tokens(approved_prefixes)
    if not prefix_token_lists:
        return False

    groups = _split_into_simple_commands(tokens)
    if groups is None:
        return False
    return all(
        any(_matches_prefix(group, prefix) for prefix in prefix_token_lists)
        for group in groups
    )


def parse_command_prefix_tokens(
    prefixes: Sequence[str],
) -> tuple[tuple[str, ...], ...]:
    """Parse configured prefixes into safe single-command token tuples.

    Preconditions: ``prefixes`` is a stage's ``approved_command_prefixes``.
    Postconditions: an entry spanning multiple lines, empty, or containing a
    shell operator is dropped rather than mis-parsed — a prefix names one
    plain command, not a chain.
    """
    parsed: list[tuple[str, ...]] = []
    for prefix in prefixes:
        if not prefix.strip() or "\n" in prefix or "\r" in prefix:
            continue
        tokens = _tokenize(prefix)
        if tokens and not any(set(token) <= _OPERATOR_CHARS for token in tokens):
            parsed.append(tokens)
    return tuple(parsed)


def _tokenize(text: str) -> tuple[str, ...]:
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        return tuple(lexer)
    except ValueError:
        return ()


def _split_into_simple_commands(
    tokens: tuple[str, ...],
) -> list[list[str]] | None:
    groups: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATORS:
            groups.append([])
            continue
        if token == "&":
            return None  # a background job — refuse, don't guess.
        if ">" in token or "<" in token:
            if _SAFE_DISCARD_REDIRECT.fullmatch(token):
                continue  # a harmless "discard to /dev/null" — drop the token.
            return None  # any other redirection — refuse.
        groups[-1].append(token)
    if any(not group for group in groups):
        return None  # an empty simple command (a stray/doubled separator).
    return groups


def _matches_prefix(tokens: list[str], prefix_tokens: tuple[str, ...]) -> bool:
    return (
        len(tokens) >= len(prefix_tokens)
        and tuple(tokens[: len(prefix_tokens)]) == prefix_tokens
    )


__all__ = ["command_is_fully_approved", "parse_command_prefix_tokens"]
