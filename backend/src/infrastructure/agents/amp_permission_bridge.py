"""Permission-bridge shim for Amp's ``delegate`` action.

Amp's permissions API has no async callback, but it does offer
``delegate`` — the action invokes a custom command and uses its exit
code to decide whether the tool runs. This shim is what the Amp CLI
invokes when ``Bash`` is delegated to us; it gates the invocation
through Atelier's existing permission UI before exiting 0 (allow) or
non-zero (deny).

Wire-up (set in ``AmpAdapter`` at agent start):

    permissions = [
        Permission(tool="Bash", action="delegate", to=<shim_script>),
    ]
    env["ATELIER_PERMISSION_SOCKET"] = "/tmp/atelier-<slug>/permission.sock"

Runtime contract (Amp CLI ⇄ delegate, observed in the Amp 2026-05 binary):

    Amp spawns the delegate with NO argv beyond its own path, writes the
    tool's input object as a JSON line to stdin, then waits for exit:
        exit 0 → allow (Amp executes the tool itself afterwards)
        exit 1 → ask  (Amp falls back to its built-in prompt — we don't
                       use this; we always answer allow/deny ourselves)
        exit * → reject (stderr surfaced to the agent as the tool result)

    Env vars Amp sets on the delegate:
        AGENT=amp
        AMP_THREAD_ID
        AGENT_TOOL_NAME       (e.g. "Bash")
        AGENT_TOOL_USE_ID

Runtime flow:

    Amp CLI ──► delegate-shim (stdin: JSON tool input)
                      │
                      ├─ connect $ATELIER_PERMISSION_SOCKET
                      ├─ send {tool, argv}  (argv reconstructed for the UI)
                      ├─ recv {decision: "allow"|"allow_always"|"deny"}
                      ├─ on allow: exit 0      (Amp runs the tool)
                      └─ on deny:  exit 2      (stderr → agent)

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

# Exit codes recognised by Amp's delegate dispatch (see ``aOR``/``rOR``
# in the Amp CLI binary). 0 lets Amp run the tool, anything else stops
# it; >1 surfaces as a "rejected by delegate" tool result.
_EXIT_ALLOW = 0
_EXIT_DENY = 2


def _die(message: str, code: int = _EXIT_DENY) -> None:
    """Print to stderr (Amp surfaces it as the tool result) and exit."""
    print(f"atelier: {message}", file=sys.stderr)
    sys.exit(code)


def _read_tool_input() -> dict[str, object]:
    """Slurp stdin (Amp writes a single JSON object then closes write
    side). Returns the parsed object, or ``{}`` if stdin was empty —
    the caller treats empty/malformed as "no command to surface" and
    fails closed."""
    raw = sys.stdin.read()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _die(f"malformed tool input from amp: {exc}; raw={raw[:200]!r}")
    if not isinstance(parsed, dict):
        _die(f"unexpected tool input shape: {type(parsed).__name__}; raw={raw[:200]!r}")
    return parsed


def _argv_for_prompt(tool_input: dict[str, object]) -> list[str]:
    """Reconstruct a bash-style argv from Amp's tool-input JSON so the
    Atelier prompt UI can render the actual command the agent is about
    to run.

    Amp's ``Bash`` input shape is ``{"cmd": "<command>"}`` — possibly
    with extra keys (cwd, timeout, etc.) that we don't surface. If the
    shape ever changes, we fall back to dumping the whole input as a
    single argv element so the user still sees *something* to decide
    on, rather than a blank prompt.
    """
    cmd = tool_input.get("cmd")
    if isinstance(cmd, str) and cmd:
        return ["-c", cmd]
    # Future-proof: Amp could rename the field. We don't want to silently
    # rubber-stamp commands the UI can't display, so encode the whole
    # input verbatim as a single token. The user sees the JSON and can
    # decide; we get a signal in the logs that the contract drifted.
    return ["-c", json.dumps(tool_input, sort_keys=True)]


def main() -> None:
    socket_path = os.environ.get(_ENV_SOCKET)
    if not socket_path:
        _die(f"permission bridge invoked without {_ENV_SOCKET}")

    tool_input = _read_tool_input()
    tool_name = os.environ.get("AGENT_TOOL_NAME", "Bash")
    forwarded = _argv_for_prompt(tool_input)
    request = {"tool": tool_name, "argv": forwarded}

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
        # Amp runs the tool itself once we exit 0. The previous "execvp
        # into bash" dance is no longer needed under the new delegate
        # contract.
        sys.exit(_EXIT_ALLOW)
    elif decision == "deny":
        _die("denied by user", code=_EXIT_DENY)
    else:
        _die(f"unexpected decision: {decision!r}")


if __name__ == "__main__":
    main()
