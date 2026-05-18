"""Shared helpers for CLI launcher provider commands."""

from __future__ import annotations


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
