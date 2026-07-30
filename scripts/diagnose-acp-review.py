#!/usr/bin/env python3
"""Compare one ACP review turn with and without an AgentTile subscriber.

This is an opt-in live diagnostic. It starts two fresh provider sessions in the
same read-only worktree with byte-identical prompts and configuration:

* ``manual_canvas`` attaches a supervisor subscription before sending input,
  matching the server-side behavior of an open AgentTile WebSocket.
* ``headless_stage`` sends the same input without a subscriber, matching a loop
  stage running while its view is closed.

The source worktree is never provisioned, reset, or cleaned. Separate diagnostic
transcripts and a JSON comparison report are written under ``--output``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from src.domain.agents import (  # noqa: E402
    SPECS,
    AgentStartContext,
    CommonAgentConfig,
    detect_shared_envs,
    render_system_prompt,
)
from src.domain.models import Persona  # noqa: E402
from src.domain.supervisor import AgentSupervisorService  # noqa: E402
from src.infrastructure.agents import ConfiguredAgentAdapterFactory  # noqa: E402
from src.infrastructure.filesystem import (  # noqa: E402
    FsTranscriptLog,
    WorkspacePaths,
)
from src.settings import get_settings  # noqa: E402

_WORK_SLUG = "ACP-DIAGNOSTIC"
_TERMINAL_TYPES = {"error", "turn_metrics"}
_VISIBLE_TYPES = {
    "error",
    "message_complete",
    "permission_request",
    "session_established",
    "status_change",
    "tool_call",
    "tool_result",
    "turn_metrics",
}


@dataclass(frozen=True)
class SourceReview:
    """Provider configuration and prompt copied from one stage agent."""

    provider: str
    model: str
    options: dict[str, Any]
    persona: Persona
    role: str
    worktree: Path
    prompt: str


@dataclass(frozen=True)
class ModeResult:
    """Bounded diagnostic result for one execution mode."""

    mode: str
    status: str
    elapsed_seconds: float
    event_count: int
    event_counts: dict[str, int]
    last_event: dict[str, Any] | None
    permission_requests: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    tool_result_sizes: list[dict[str, Any]]
    transcript: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the same ACP review twice in one worktree: once with an "
            "AgentTile-style subscriber and once headlessly."
        )
    )
    parser.add_argument(
        "--source-agent-dir",
        type=Path,
        required=True,
        help="Agent directory containing agent.json and transcript.ndjson.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Diagnostic output root (default: /tmp/atelier-acp-review-<timestamp>).",
    )
    parser.add_argument(
        "--inactivity-seconds",
        type=float,
        default=300,
        help="Classify a turn as stalled after this many seconds without an event.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=900,
        help="Hard per-mode deadline.",
    )
    parser.add_argument(
        "--order",
        choices=("manual-first", "headless-first"),
        default="manual-first",
        help="Sequential execution order; sequential avoids provider-load interference.",
    )
    parser.add_argument(
        "--approved-prefix",
        action="append",
        default=[],
        help="Optional provider-native approved command prefix; repeat as needed.",
    )
    parser.add_argument(
        "--allow-write-mode",
        action="store_true",
        help="Allow a source agent whose provider mode is not read-only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the comparison inputs without launching providers.",
    )
    return parser.parse_args()


def load_source(agent_dir: Path) -> SourceReview:
    """Load one persisted stage agent and its original stage request."""
    metadata_path = agent_dir / "agent.json"
    transcript_path = agent_dir / "transcript.ndjson"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    prompt = _original_stage_prompt(transcript_path)
    worktree_slug = str(metadata.get("worktree_slug") or metadata["slug"])
    worktree = agent_dir.resolve().parents[1] / "worktrees" / worktree_slug
    if not worktree.is_dir():
        raise ValueError(f"worktree not found: {worktree}")
    return SourceReview(
        provider=str(metadata["provider"]),
        model=str(metadata["model"]),
        options=dict(metadata.get("options") or {}),
        persona=cast(Persona, str(metadata["persona"])),
        role=str(metadata["role"]),
        worktree=worktree,
        prompt=prompt,
    )


def _original_stage_prompt(path: Path) -> str:
    """Return the initial loop-stage request from a persisted transcript."""
    fallback = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        event = json.loads(raw)
        if event.get("type") != "user_input":
            continue
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        fallback = fallback or text
        if "Execute loop run `" in text and "stage `" in text:
            return text
    if fallback:
        return fallback
    raise ValueError(f"no user_input found in transcript: {path}")


def _is_detached(worktree: Path) -> bool:
    result = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=worktree,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode != 0


def _git_status(worktree: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=worktree,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git status failed")
    return result.stdout


def _event_summary(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "unknown")
    if event_type == "tool_call":
        return str(event.get("name") or event.get("title") or "")[:160]
    if event_type == "tool_result":
        return f"{len(str(event.get('content') or ''))} chars"
    if event_type == "permission_request":
        return str(event.get("tool_name") or "")[:160]
    if event_type == "error":
        return str(event.get("message") or "")[:200]
    if event_type == "status_change":
        return str(event.get("status") or "")
    if event_type == "message_complete":
        return str(event.get("text") or "")[:200].replace("\n", " ")
    return ""


async def _drain_subscription(subscription: Any) -> None:
    """Drain the same supervisor subscription used by an AgentTile WebSocket."""
    async for _event in subscription.stream():
        pass


async def run_mode(
    source: SourceReview,
    *,
    mode: str,
    output: Path,
    system_prompt: str,
    approved_prefixes: tuple[str, ...],
    inactivity_seconds: float,
    timeout_seconds: float,
) -> ModeResult:
    """Run one live ACP turn and return its bounded event diagnostics."""
    paths = WorkspacePaths(output)
    transcript_log = FsTranscriptLog(paths)
    supervisor = AgentSupervisorService(transcript_log)
    common = CommonAgentConfig(
        workdir=source.worktree,
        system_prompt=system_prompt,
        approved_command_prefixes=approved_prefixes,
    )
    config = SPECS[source.provider].build(common, source.model, source.options)
    adapter = ConfiguredAgentAdapterFactory(get_settings()).build(config)
    agent_slug = mode.replace("_", "-")
    await supervisor.register_agent(
        _WORK_SLUG,
        agent_slug,
        adapter,
        AgentStartContext(
            workdir=source.worktree,
            model=source.model,
            system_prompt=common.system_prompt,
        ),
    )

    started = time.monotonic()
    cursor = 0
    last_event_at = started
    events: list[dict[str, Any]] = []
    status = "timeout"
    subscription_context = (
        supervisor.subscribe(agent_slug, cursor=0)
        if mode == "manual_canvas"
        else _null_subscription()
    )
    try:
        async with subscription_context as subscription:
            drain_task = (
                asyncio.create_task(_drain_subscription(subscription))
                if subscription is not None
                else None
            )
            try:
                await supervisor.send_input(agent_slug, source.prompt)
                while True:
                    await asyncio.sleep(0.2)
                    new_events = list(
                        transcript_log.read_from_cursor(
                            _WORK_SLUG, agent_slug, cursor
                        )
                    )
                    for event in new_events:
                        cursor = max(cursor, int(event["seq"]))
                        events.append(event)
                        last_event_at = time.monotonic()
                        if event.get("type") in _VISIBLE_TYPES:
                            print(
                                f"[{mode}] +{last_event_at - started:7.1f}s "
                                f"#{event['seq']} {event.get('type')} "
                                f"{_event_summary(event)}",
                                flush=True,
                            )
                    if any(event.get("type") in _TERMINAL_TYPES for event in new_events):
                        status = (
                            "error"
                            if any(event.get("type") == "error" for event in new_events)
                            else "completed"
                        )
                        break
                    if any(
                        event.get("type") == "permission_request"
                        for event in new_events
                    ):
                        status = "permission_required"
                        break
                    now = time.monotonic()
                    if now - last_event_at >= inactivity_seconds:
                        status = "stalled"
                        break
                    if now - started >= timeout_seconds:
                        status = "timeout"
                        break
            finally:
                if drain_task is not None:
                    drain_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await drain_task
    finally:
        await supervisor.shutdown()

    transcript = paths.transcript(_WORK_SLUG, agent_slug)
    return ModeResult(
        mode=mode,
        status=status,
        elapsed_seconds=round(time.monotonic() - started, 3),
        event_count=len(events),
        event_counts=dict(sorted(Counter(str(e.get("type")) for e in events).items())),
        last_event=_bounded_event(events[-1]) if events else None,
        permission_requests=[
            _bounded_event(event)
            for event in events
            if event.get("type") == "permission_request"
        ],
        tool_calls=[
            {
                "seq": event.get("seq"),
                "name": event.get("name"),
                "title": event.get("title"),
            }
            for event in events
            if event.get("type") == "tool_call"
        ],
        tool_result_sizes=[
            {
                "seq": event.get("seq"),
                "characters": len(str(event.get("content") or "")),
                "is_error": bool(event.get("is_error")),
            }
            for event in events
            if event.get("type") == "tool_result"
        ],
        transcript=str(transcript),
    )


class _null_subscription:
    """Async context manager matching ``subscribe`` without attaching a client."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: Any) -> None:
        return None


def _bounded_event(event: dict[str, Any]) -> dict[str, Any]:
    """Keep reports readable without copying provider payloads."""
    return {
        "seq": event.get("seq"),
        "ts": event.get("ts"),
        "type": event.get("type"),
        "status": event.get("status"),
        "summary": _event_summary(event),
    }


async def async_main(args: argparse.Namespace) -> int:
    source = load_source(args.source_agent_dir)
    if source.provider not in SPECS:
        raise ValueError(f"unsupported provider: {source.provider}")
    if source.options.get("mode") != "read-only" and not args.allow_write_mode:
        raise ValueError(
            "source provider mode is not read-only; pass --allow-write-mode "
            "only when modifying this worktree is acceptable"
        )
    if args.inactivity_seconds <= 0 or args.timeout_seconds <= 0:
        raise ValueError("timeouts must be positive")

    output = args.output or Path(
        f"/tmp/atelier-acp-review-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    output.mkdir(parents=True, exist_ok=False)
    prompt_hash = hashlib.sha256(source.prompt.encode()).hexdigest()
    system_prompt = render_system_prompt(
        source.persona,
        source.role,
        workdir=source.worktree,
        is_detached_worktree=_is_detached(source.worktree),
        shared_envs=detect_shared_envs(source.worktree),
    )
    status_before = _git_status(source.worktree)
    order = (
        ("manual_canvas", "headless_stage")
        if args.order == "manual-first"
        else ("headless_stage", "manual_canvas")
    )
    inputs = {
        "source_agent_dir": str(args.source_agent_dir.resolve()),
        "worktree": str(source.worktree),
        "provider": source.provider,
        "model": source.model,
        "options": source.options,
        "prompt_sha256": prompt_hash,
        "prompt_characters": len(source.prompt),
        "system_prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
        "system_prompt_characters": len(system_prompt),
        "approved_prefixes": args.approved_prefix,
        "order": list(order),
        "inactivity_seconds": args.inactivity_seconds,
        "timeout_seconds": args.timeout_seconds,
    }
    print(json.dumps(inputs, indent=2), flush=True)
    if args.dry_run:
        (output / "report.json").write_text(
            json.dumps({"inputs": inputs, "results": []}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"dry run complete: {output / 'report.json'}")
        return 0

    results = []
    for mode in order:
        print(f"\nStarting {mode}...", flush=True)
        results.append(
            await run_mode(
                source,
                mode=mode,
                output=output,
                system_prompt=system_prompt,
                approved_prefixes=tuple(args.approved_prefix),
                inactivity_seconds=args.inactivity_seconds,
                timeout_seconds=args.timeout_seconds,
            )
        )

    status_after = _git_status(source.worktree)
    report = {
        "inputs": inputs,
        "worktree_unchanged": status_after == status_before,
        "results": [asdict(result) for result in results],
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nReport: {report_path}")
    print(
        "Results: "
        + ", ".join(f"{result.mode}={result.status}" for result in results)
    )
    if status_after != status_before:
        print("WARNING: worktree status changed during the read-only diagnostic.")
        return 2
    return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
