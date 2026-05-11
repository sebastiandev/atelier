"""Round-trip tests for ``amp_permission_bridge.py``.

The bridge is a standalone subprocess that the Amp CLI invokes when a
Bash tool call hits a ``delegate`` permission rule. Under the current
Amp contract (observed in the 2026-05 binary):

  * Amp spawns the delegate with NO argv beyond its own path
  * tool input is written to stdin as a JSON line
  * exit code drives the decision: 0 = allow, non-zero = reject

We exercise the bridge end-to-end by spawning it with a fake supervisor
on the other end of a Unix socket — the same shape the production
AmpAdapter wires.
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
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", "")},
        )
        _out, err = await proc.communicate(input=b'{"cmd": "echo hi"}')
        return proc.returncode or 0, err

    code, err = asyncio.run(run())
    assert code == 2
    assert b"ATELIER_PERMISSION_SOCKET" in err


def test_bridge_exits_zero_on_allow_and_forwards_command_to_supervisor() -> None:
    """On allow, the bridge exits 0 (Amp then runs the tool itself).
    The supervisor sees the reconstructed bash argv so the prompt UI
    can render the command the agent is about to run."""

    async def run() -> tuple[int, dict | None]:
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
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "ATELIER_PERMISSION_SOCKET": socket_path},
            )
            _out, _err = await proc.communicate(
                input=b'{"cmd": "echo allowed-output"}'
            )
            return proc.returncode or 0, received
        finally:
            server.close()
            await server.wait_closed()
            shutil.rmtree(sock_dir, ignore_errors=True)

    code, received = asyncio.run(run())
    assert code == 0
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
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "ATELIER_PERMISSION_SOCKET": socket_path},
            )
            _out, err = await proc.communicate(input=b'{"cmd": "echo blocked"}')
            return proc.returncode or 0, err
        finally:
            server.close()
            await server.wait_closed()
            shutil.rmtree(sock_dir, ignore_errors=True)

    code, err = asyncio.run(run())
    assert code != 0
    assert b"denied by user" in err


def test_bridge_fails_closed_on_unreachable_socket(tmp_path: Path) -> None:
    """Socket env points to a non-existent path: bridge surfaces stderr,
    exits non-zero. Amp surfaces stderr as the tool result so the agent
    sees the reason rather than a silent allow."""

    async def run() -> tuple[int, bytes]:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(_BRIDGE),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "ATELIER_PERMISSION_SOCKET": str(tmp_path / "does-not-exist.sock"),
            },
        )
        _out, err = await proc.communicate(input=b'{"cmd": "echo never-runs"}')
        return proc.returncode or 0, err

    code, err = asyncio.run(run())
    assert code != 0
    assert b"cannot reach permission socket" in err


def test_bridge_falls_back_to_json_dump_when_cmd_field_missing() -> None:
    """If Amp's tool-input shape ever drifts (no ``cmd`` field), the
    bridge encodes the whole input as a single argv token so the
    prompt UI still shows something rather than rubber-stamping a
    command the user can't see."""

    async def run() -> tuple[int, dict | None]:
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
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "ATELIER_PERMISSION_SOCKET": socket_path},
            )
            _out, _err = await proc.communicate(
                input=b'{"shell_command": "ls -la", "cwd": "/tmp"}'
            )
            return proc.returncode or 0, received
        finally:
            server.close()
            await server.wait_closed()
            shutil.rmtree(sock_dir, ignore_errors=True)

    code, received = asyncio.run(run())
    assert code == 0
    assert received is not None
    assert received["tool"] == "Bash"
    assert received["argv"][0] == "-c"
    assert "shell_command" in received["argv"][1]
