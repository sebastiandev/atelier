"""Workaround for amp_sdk's 64 KiB stdout-line buffer limit.

NOTE
====

Amp's CLI streams agent events on stdout as one JSON object per line
(stream-json mode). ``amp_sdk.core._read_process_output`` consumes that
stream via ``proc.stdout.readline()`` — and the underlying
``asyncio.StreamReader`` ships with the default 64 KiB buffer
(``asyncio.streams._DEFAULT_LIMIT``).

A single JSON line larger than 64 KiB triggers
``asyncio.LimitOverrunError("Separator is not found, and chunk exceed
the limit", ...)`` and the SDK pump dies mid-turn. The user sees the
error surface as a tool result (Atelier wraps the exception via
``Error(message=str(e))`` in ``amp_adapter._run_sdk_pump``), the adapter
shuts down, and the conversation can't continue.

In practice we hit this with tool calls whose result payload is large
— e.g. ``rg -l <pattern> <large-tree>`` returning hundreds of file
paths, or a ``Read`` against a multi-megabyte file. Amp serialises the
whole tool result into one stream-json line, so payload size and line
size are the same number.

Patch strategy
--------------

We wrap ``_read_process_output`` and bump ``proc.stdout._limit`` from
64 KiB up to 64 MiB BEFORE the read loop begins. ``_limit`` is a
private attribute of ``asyncio.StreamReader`` that the readuntil/
readline path consults; setting it higher gives those calls more room
before they raise ``LimitOverrunError``. The amp_sdk-side parsing and
routing logic is untouched.

Upstream
--------

The proper fix is for ``amp_sdk`` to accept a larger limit (either as
a configurable kwarg on ``execute()`` or simply by bumping the default
to multi-MiB, since each line is a self-contained JSON message that
the SDK is already going to load into memory anyway).

Upstream report / PR pending — see docs/backend.md ("Known upstream
workarounds") for the link and a reminder to drop this module once
the dep is bumped.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import amp_sdk.core as _amp_core

# 64 MiB — comfortably larger than any plausible single tool result.
# An ``rg -l`` against a 1M-file tree at ~120 char/path averages ~120 MB
# but anyone running that almost certainly wants to narrow the search
# regardless. 64 MiB clears the realistic ceiling (kernel sources,
# generated SQL dumps, etc.) without letting a runaway tool eat
# unbounded RAM.
_LARGER_BUFFER = 64 * 1024 * 1024

_ORIGINAL_READ_PROCESS_OUTPUT = _amp_core._read_process_output

_PATCH_MARKER = "__atelier_amp_sdk_patched__"


async def _read_process_output_unbounded(proc: Any) -> AsyncIterator[Any]:
    """Bump the stdout StreamReader's ``_limit`` before delegating to
    the upstream reader. Idempotent — calling this on a process whose
    reader was already bumped is a no-op."""
    stream = getattr(proc, "stdout", None)
    if stream is not None:
        current = getattr(stream, "_limit", 0)
        if current < _LARGER_BUFFER:
            stream._limit = _LARGER_BUFFER
    async for msg in _ORIGINAL_READ_PROCESS_OUTPUT(proc):
        yield msg


def install() -> None:
    """Apply the patch. Idempotent; safe to call from multiple modules
    or repeatedly across reloads."""
    if getattr(_amp_core, _PATCH_MARKER, False):
        return
    setattr(_amp_core, _PATCH_MARKER, True)
    _amp_core._read_process_output = _read_process_output_unbounded


__all__ = ["install"]
