"""AgentEvent tagged union.

Each variant carries a `type` discriminator so consumers (supervisor,
WS frame encoder, transcript writer) can dispatch via a `match` or
`isinstance`. Adapters normalise their native SDK shapes into this
union — that normalisation is the entire point of the abstraction.

Sequence numbers (`seq`) and persistence are owned by the supervisor
(STORY-009), not the adapter — these in-memory events carry only the
adapter's own timestamp.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True, kw_only=True)
class MessageDelta:
    """Streaming chunk of an assistant message. Multiple per message."""

    type: Literal["message_delta"] = "message_delta"
    ts: datetime
    text: str


@dataclass(frozen=True, kw_only=True)
class MessageComplete:
    """Final, fully-assembled assistant message."""

    type: Literal["message_complete"] = "message_complete"
    ts: datetime
    text: str


@dataclass(frozen=True, kw_only=True)
class ThinkingDelta:
    """Streaming chunk of an assistant reasoning/thinking block.

    Emitted by adapters whose underlying SDK exposes thinking content as
    a distinct stream (currently Claude). Adapters whose SDK does not
    surface thinking simply never emit this variant.
    """

    type: Literal["thinking_delta"] = "thinking_delta"
    ts: datetime
    text: str


@dataclass(frozen=True, kw_only=True)
class ThinkingComplete:
    """Final, fully-assembled assistant thinking block."""

    type: Literal["thinking_complete"] = "thinking_complete"
    ts: datetime
    text: str


@dataclass(frozen=True, kw_only=True)
class ToolCall:
    """The assistant invoked a tool.

    ``name`` and ``arguments`` follow Atelier's canonical tool shape
    regardless of which provider SDK emitted the call. Adapters call
    ``infrastructure.agents.tool_canonical.canonicalize_tool`` before
    yielding so the supervisor, transcript ledger, and frontend renderer
    target a single contract per tool concept:

    - ``Bash``      ``command``, optional ``cwd``, ``description``,
                    ``run_in_background``, ``timeout``
    - ``Edit``      ``path``, ``old_text``, ``new_text``,
                    optional ``replace_all``
    - ``MultiEdit`` ``path``, ``edits[]`` — each
                    ``{old_text, new_text, replace_all?}``
    - ``Read``      ``path``, optional ``line_range`` (``"1-100"`` or ``"1+"``)
    - ``Write``     ``path``, ``content``
    - ``Grep``      ``pattern``, optional ``path``
    - ``Glob``      ``pattern``, optional ``path``

    Tools without a canonical concept pass through with their raw
    provider shape — the frontend falls back to a generic JSON view.
    """

    type: Literal["tool_call"] = "tool_call"
    ts: datetime
    tool_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class ToolResult:
    """A tool returned. `tool_id` matches the originating ToolCall."""

    type: Literal["tool_result"] = "tool_result"
    ts: datetime
    tool_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, kw_only=True)
class StatusChange:
    """Agent transitioned between live / thinking / idle."""

    type: Literal["status_change"] = "status_change"
    ts: datetime
    status: Literal["live", "thinking", "idle"]


@dataclass(frozen=True, kw_only=True)
class ArtifactMarker:
    """The agent emitted an `atelier_artifact` payload. The supervisor's
    write-through pipeline pattern-matches on this and records an Artifact."""

    type: Literal["artifact_marker"] = "artifact_marker"
    ts: datetime
    payload: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class Error:
    """Adapter or upstream SDK signalled an error."""

    type: Literal["error"] = "error"
    ts: datetime
    message: str


@dataclass(frozen=True, kw_only=True)
class SessionEstablished:
    """Adapter has assigned (or resumed) a provider session/thread.

    The supervisor catches this and persists ``session_id`` on the agent
    row so the same conversation can be resumed on a future reconnect.
    Adapters emit one as soon as the SDK surfaces an ID — Claude on the
    first ``ResultMessage``, Amp on the init ``SystemMessage``.
    """

    type: Literal["session_established"] = "session_established"
    ts: datetime
    session_id: str


PermissionDecisionValue = Literal["allow", "allow_always", "deny"]


@dataclass(frozen=True, kw_only=True)
class PermissionRequest:
    """The adapter is asking the user whether a tool may run.

    Emitted from the SDK's ``can_use_tool`` callback before the tool
    invocation proceeds. ``request_id`` is opaque to the supervisor;
    the user's decision is routed back through ``adapter.resolve_permission``
    keyed on the same id.
    """

    type: Literal["permission_request"] = "permission_request"
    ts: datetime
    request_id: str
    tool_name: str
    tool_input: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class PermissionDecision:
    """The user's answer to a ``PermissionRequest``.

    Emitted by the adapter once the corresponding future has been
    resolved, so the transcript holds both halves of the exchange.
    """

    type: Literal["permission_decision"] = "permission_decision"
    ts: datetime
    request_id: str
    decision: PermissionDecisionValue


@dataclass(frozen=True, kw_only=True)
class TurnMetrics:
    """Per-turn rollup: duration + token usage. Adapters emit one of
    these immediately before the trailing ``StatusChange("idle")`` so
    consumers can render "8m 42s · ↓ 32.9k tokens" the way the Claude
    Code CLI does. Adapters whose SDK doesn't expose a field leave it
    at its default (zero / None).

    Two distinct flavours of "tokens" live on this event:

    - ``input_tokens`` / ``output_tokens`` / ``cache_*_input_tokens`` are
      the **cumulative** counts across every model sub-call in this turn
      (a turn that runs 20 tool-uses makes 20 API calls and the SDK's
      ``ResultMessage`` aggregates their usage). These are what
      consumers sum across turns to compute session **cost** — that
      sum equals what Anthropic actually billed.

    - ``last_prompt_tokens`` is the prompt size of the **last** model
      call in the turn. The name reads "per-prompt", but each sub-call's
      prompt contains the *entire conversation history so far* (system +
      every prior user/assistant/tool-use/tool-result + this turn's new
      user message + any in-turn tool round-trips). So this value IS the
      running total of context currently occupying the model's window
      — the "should I /clear?" number, growing monotonically across
      turns. Consumers use it for **context %** display.

      Why not sum ``input + cache_read + cache_creation``? Each sub-call
      replays the full history (read from cache), so summing across N
      sub-calls multiplies the same content N times. ``last_prompt_tokens``
      is a one-frame snapshot taken at end-of-turn, after every sub-call
      has fired.
    """

    type: Literal["turn_metrics"] = "turn_metrics"
    ts: datetime
    duration_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    last_prompt_tokens: int = 0
    model: str | None = None


AgentEvent = (
    MessageDelta
    | MessageComplete
    | ThinkingDelta
    | ThinkingComplete
    | ToolCall
    | ToolResult
    | StatusChange
    | ArtifactMarker
    | Error
    | SessionEstablished
    | PermissionRequest
    | PermissionDecision
    | TurnMetrics
)


__all__ = [
    "AgentEvent",
    "ArtifactMarker",
    "Error",
    "MessageComplete",
    "MessageDelta",
    "PermissionDecision",
    "PermissionDecisionValue",
    "PermissionRequest",
    "SessionEstablished",
    "StatusChange",
    "ThinkingComplete",
    "ThinkingDelta",
    "ToolCall",
    "ToolResult",
    "TurnMetrics",
]
