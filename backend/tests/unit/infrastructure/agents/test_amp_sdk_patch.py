"""Tests for the ``_amp_sdk_patch`` shim.

The patch raises ``amp_sdk``'s default stdout-line buffer limit (64 KiB)
that otherwise tears the adapter down mid-turn on bulky tool results.
See ``_amp_sdk_patch.py``'s NOTE block for the upstream context.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import amp_sdk.core as _amp_core

from src.infrastructure.agents import _amp_sdk_patch


def test_install_replaces_amp_sdk_reader() -> None:
    """Calling ``install`` swaps ``amp_sdk.core._read_process_output``
    for our wrapper and marks the module so a second call is a no-op."""
    _amp_sdk_patch.install()
    assert _amp_core._read_process_output is _amp_sdk_patch._read_process_output_unbounded
    assert getattr(_amp_core, _amp_sdk_patch._PATCH_MARKER) is True
    # Idempotent — second call doesn't re-wrap or otherwise mutate state.
    before = _amp_core._read_process_output
    _amp_sdk_patch.install()
    assert _amp_core._read_process_output is before


def test_patched_reader_bumps_stream_limit() -> None:
    """The wrapper sets ``proc.stdout._limit`` to our larger value
    before delegating, so subsequent ``readline`` / ``readuntil`` calls
    can buffer JSON lines well past the 64 KiB upstream default."""

    class _FakeStream:
        def __init__(self) -> None:
            self._limit = 64 * 1024  # asyncio's default

    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = _FakeStream()

    proc = _FakeProc()

    # Stub the wrapped upstream so the test stays isolated from
    # amp_sdk's real read loop (which would block on a real stdout).
    async def _empty(_proc: Any):  # type: ignore[no-untyped-def]
        if False:
            yield  # pragma: no cover

    original = _amp_sdk_patch._ORIGINAL_READ_PROCESS_OUTPUT
    _amp_sdk_patch._ORIGINAL_READ_PROCESS_OUTPUT = _empty
    try:
        async def run() -> None:
            async for _ in _amp_sdk_patch._read_process_output_unbounded(proc):
                pass

        asyncio.run(run())
    finally:
        _amp_sdk_patch._ORIGINAL_READ_PROCESS_OUTPUT = original

    assert proc.stdout._limit == _amp_sdk_patch._LARGER_BUFFER


def test_patched_reader_tolerates_missing_stdout() -> None:
    """Some test/edge paths spawn a Process without piped stdout. The
    patch must not raise — it simply skips the bump and delegates."""

    class _NoStdoutProc:
        stdout = None

    async def _empty(_proc: Any):  # type: ignore[no-untyped-def]
        if False:
            yield  # pragma: no cover

    original = _amp_sdk_patch._ORIGINAL_READ_PROCESS_OUTPUT
    _amp_sdk_patch._ORIGINAL_READ_PROCESS_OUTPUT = _empty
    try:
        async def run() -> None:
            async for _ in _amp_sdk_patch._read_process_output_unbounded(
                _NoStdoutProc()
            ):
                pass

        # Should complete without exception.
        asyncio.run(run())
    finally:
        _amp_sdk_patch._ORIGINAL_READ_PROCESS_OUTPUT = original


def test_patched_reader_does_not_lower_existing_limit() -> None:
    """If something else (a different patch, a future amp_sdk default)
    already raised the limit above ours, leave it alone."""

    class _FakeStream:
        def __init__(self) -> None:
            self._limit = _amp_sdk_patch._LARGER_BUFFER * 2

    class _FakeProc:
        stdout = _FakeStream()

    proc = _FakeProc()
    starting = proc.stdout._limit

    async def _empty(_proc: Any):  # type: ignore[no-untyped-def]
        if False:
            yield  # pragma: no cover

    original = _amp_sdk_patch._ORIGINAL_READ_PROCESS_OUTPUT
    _amp_sdk_patch._ORIGINAL_READ_PROCESS_OUTPUT = _empty
    try:
        async def run() -> None:
            async for _ in _amp_sdk_patch._read_process_output_unbounded(proc):
                pass

        asyncio.run(run())
    finally:
        _amp_sdk_patch._ORIGINAL_READ_PROCESS_OUTPUT = original

    assert proc.stdout._limit == starting
