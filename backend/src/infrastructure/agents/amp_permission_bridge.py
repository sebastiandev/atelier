"""Permission-bridge shim for Amp's ``delegate`` action.

Amp's permissions API has no async callback, but it does offer
``delegate`` — the action substitutes a custom command for the tool's
native execution. This shim is what the Amp CLI invokes in place of
``bash -c <command>`` when ``Bash`` is delegated to us; it gates the
invocation through Atelier's existing permission UI before running it.

Wire-up (set in ``AmpAdapter`` at agent start):

    permissions = [
        Permission(tool="Bash", action="delegate",
                   to=f"{python_exe} {bridge_path}"),
    ]
    env["ATELIER_PERMISSION_SOCKET"] = "/tmp/atelier-<slug>.sock"

Runtime flow:

    Amp CLI ──► python bridge.py -c "<command>"
                      │
                      ├─ connect $ATELIER_PERMISSION_SOCKET
                      ├─ send {tool:"Bash", argv:["-c","<command>"]}
                      ├─ recv {decision:"allow"|"deny"}
                      ├─ on allow: os.execvp("bash", argv)  ← we BECOME bash
                      └─ on deny:  print to stderr, exit 1

``execvp`` is load-bearing: it replaces this process image with bash,
so Amp sees the real bash exit code, stdout, and stderr exactly as if
it had shelled out itself. No double-fork, no tee, no signal weirdness.

Stdlib only: this script ships in the same package but runs as a
detached subprocess of the Amp CLI. It must not import any Atelier
modules — the CLI's invocation env is hostile (different cwd, may
miss our virtualenv depending on how Amp was launched).

Failure modes are deliberately fail-closed: missing env var, missing
socket, malformed handshake all exit non-zero with a stderr message
that Amp surfaces as the tool result. Better the agent sees "tool
denied" than the bridge silently allows an unreviewed command.
"""

from __future__ import annotations

import json
import os
import socket
import sys

_ENV_SOCKET = "ATELIER_PERMISSION_SOCKET"
_HANDSHAKE_TIMEOUT_SEC = 1.0  # connect + initial write
_DECISION_TIMEOUT_SEC: float | None = None  # user can take as long as they need


def _die(message: str, code: int = 2) -> None:
    """Print to stderr (Amp surfaces it as the tool result) and exit."""
    print(f"atelier: {message}", file=sys.stderr)
    sys.exit(code)


def main(argv: list[str]) -> None:
    socket_path = os.environ.get(_ENV_SOCKET)
    if not socket_path:
        _die(f"permission bridge invoked without {_ENV_SOCKET}")

    if len(argv) < 2:
        _die("permission bridge invoked with no tool args")

    # ``argv[1:]`` is whatever Amp would have passed to ``bash`` — typically
    # ``["-c", "<command>"]`` but we don't hard-code that; whatever shape
    # Amp uses, we forward verbatim on allow.
    forwarded = argv[1:]

    request = {"tool": "Bash", "argv": forwarded}

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(_HANDSHAKE_TIMEOUT_SEC)
        try:
            sock.connect(socket_path)
        except OSError as exc:
            _die(f"cannot reach permission socket {socket_path}: {exc}")

        try:
            sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        except OSError as exc:
            _die(f"could not send permission request: {exc}")

        sock.settimeout(_DECISION_TIMEOUT_SEC)
        # The supervisor sends one JSON line and closes write side; read
        # until EOF so a partial response is detected as malformed.
        chunks: list[bytes] = []
        while True:
            try:
                buf = sock.recv(4096)
            except OSError as exc:
                _die(f"permission socket read failed: {exc}")
            if not buf:
                break
            chunks.append(buf)
            if b"\n" in buf:
                break
        raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
    finally:
        sock.close()

    try:
        response = json.loads(raw)
    except json.JSONDecodeError:
        _die(f"malformed permission response: {raw!r}")

    decision = response.get("decision") if isinstance(response, dict) else None
    if decision in ("allow", "allow_always"):
        # Replace ourselves with bash. From Amp's perspective the
        # delegate target IS bash — same exit code, stdout, stderr.
        try:
            os.execvp("bash", ["bash"] + forwarded)
        except OSError as exc:
            _die(f"failed to exec bash: {exc}")
    elif decision == "deny":
        _die("denied by user", code=1)
    else:
        _die(f"unexpected decision: {decision!r}")


if __name__ == "__main__":
    main(sys.argv)
