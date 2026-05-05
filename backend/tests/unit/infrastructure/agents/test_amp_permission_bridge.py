"""Round-trip tests for ``amp_permission_bridge.py``.

The bridge is a standalone subprocess that the Amp CLI invokes in place
of ``bash -c``. We exercise it end-to-end by spawning it as a child
with a fake supervisor on the other end of a Unix socket — the same
shape the production AmpAdapter wires.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_BRIDGE = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "infrastructure"
    / "agents"
    / "amp_permission_bridge.py"
)


def test_bridge_exits_with_message_when_socket_env_missing() -> None:
    """Fail-closed when ATELIER_PERMISSION_SOCKET is unset."""

    async def run() -> tuple[int, bytes]:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(_BRIDGE),
            "-c",
            "echo hi",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", "")},
        )
        _out, err = await proc.communicate()
        return proc.returncode or 0, err

    code, err = asyncio.run(run())
    assert code == 2
    assert b"ATELIER_PERMISSION_SOCKET" in err


def test_bridge_execs_bash_on_allow() -> None:
    """On allow, the bridge ``execvp``s into bash. The tool result the
    agent sees is whatever bash printed."""

    async def run() -> tuple[int, bytes, bytes, dict | None]:
        # macOS AF_UNIX limit (~104 chars) bites pytest's tmp_path; use
        # a short tmpdir instead.
        import shutil
        import tempfile

        sock_dir = tempfile.mkdtemp(prefix="amp-bridge-")
        socket_path = os.path.join(sock_dir, "p.sock")
        received: dict | None = None

        async def handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            nonlocal received
            line = await reader.readline()
            received = json.loads(line.decode("utf-8").strip())
            writer.write(b'{"decision":"allow"}\n')
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=socket_path)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(_BRIDGE),
                "-c",
                "echo allowed-output",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "ATELIER_PERMISSION_SOCKET": socket_path},
            )
            out, err = await proc.communicate()
            return proc.returncode or 0, out, err, received
        finally:
            server.close()
            await server.wait_closed()
            shutil.rmtree(sock_dir, ignore_errors=True)

    code, out, _err, received = asyncio.run(run())
    assert code == 0
    assert out.strip() == b"allowed-output"
    assert received == {"tool": "Bash", "argv": ["-c", "echo allowed-output"]}


def test_bridge_exits_nonzero_on_deny() -> None:
    async def run() -> tuple[int, bytes]:
        import shutil
        import tempfile

        sock_dir = tempfile.mkdtemp(prefix="amp-bridge-")
        socket_path = os.path.join(sock_dir, "p.sock")

        async def handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await reader.readline()
            writer.write(b'{"decision":"deny"}\n')
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=socket_path)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(_BRIDGE),
                "-c",
                "echo blocked",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "ATELIER_PERMISSION_SOCKET": socket_path},
            )
            _out, err = await proc.communicate()
            return proc.returncode or 0, err
        finally:
            server.close()
            await server.wait_closed()
            shutil.rmtree(sock_dir, ignore_errors=True)

    code, err = asyncio.run(run())
    assert code == 1
    assert b"denied by user" in err


def test_bridge_fails_closed_on_unreachable_socket(tmp_path: Path) -> None:
    """Socket env points to a non-existent path: bridge surfaces stderr,
    exits non-zero. Amp will treat the missing tool result as a failure
    and the agent gets the message."""

    async def run() -> tuple[int, bytes]:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(_BRIDGE),
            "-c",
            "echo never-runs",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "ATELIER_PERMISSION_SOCKET": str(tmp_path / "does-not-exist.sock"),
            },
        )
        _out, err = await proc.communicate()
        return proc.returncode or 0, err

    code, err = asyncio.run(run())
    assert code != 0
    assert b"cannot reach permission socket" in err
