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

    ``kind`` / ``title`` / ``locations`` are optional ACP enrichment
    (STORY-033): ``kind`` is the protocol's tool category (``read`` /
    ``edit`` / ``execute`` / ...), ``title`` a human-readable label,
    ``locations`` a list of ``{path, line?}`` dicts for follow-the-agent
    UX. Adapters without this granularity leave them ``None`` and the
    serializer omits them, keeping legacy transcript lines byte-stable.
    """

    type: Literal["tool_call"] = "tool_call"
    ts: datetime
    tool_id: str
    name: str
    arguments: dict[str, Any]
    kind: str | None = None
    title: str | None = None
    locations: tuple[dict[str, Any], ...] | None = None


@dataclass(frozen=True, kw_only=True)
class ToolCallUpdate:
    """Mid-flight update to a running tool call (ACP granularity).

    ``tool_id`` matches the originating ``ToolCall``. Only the fields
    that actually changed are set; unset fields serialize away. The
    terminal outcome of a tool still arrives as ``ToolResult`` — this
    event exists so the frontend can move a tool card through
    pending → in_progress and surface live ``locations`` without
    waiting for completion. Adapters must coalesce noisy streams; one
    event per meaningful transition, not one per output byte.
    """

    type: Literal["tool_call_update"] = "tool_call_update"
    ts: datetime
    tool_id: str
    status: str | None = None
    title: str | None = None
    kind: str | None = None
    locations: tuple[dict[str, Any], ...] | None = None


@dataclass(frozen=True, kw_only=True)
class ToolResult:
    """A tool returned. `tool_id` matches the originating ToolCall.

    ``diff`` is optional ACP enrichment: a ``{path, old_text, new_text}``
    dict (``old_text`` is ``None`` for new files) extracted from the
    protocol's structured diff content. It lets the frontend render the
    diff viewer even for tools that aren't canonical Edit/MultiEdit.
    """

    type: Literal["tool_result"] = "tool_result"
    ts: datetime
    tool_id: str
    content: str
    is_error: bool = False
    diff: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class PlanUpdate:
    """The agent published or revised its plan (ACP ``plan`` update).

    Full-replacement semantics per the protocol: ``entries`` is the
    complete current plan, not a delta — consumers replace any prior
    rendered plan wholesale. Each entry is ``{content, priority, status}``
    with priority ∈ high|medium|low and status ∈ pending|in_progress|
    completed.
    """

    type: Literal["plan_update"] = "plan_update"
    ts: datetime
    entries: tuple[dict[str, Any], ...]


@dataclass(frozen=True, kw_only=True)
class ModeChange:
    """The agent's session mode changed (ACP ``current_mode_update``).

    Emitted both for agent-initiated switches (e.g. a ``switch_mode``
    tool) and as confirmation after a client-side ``session/set_mode``.
    Rendered as an informational chip; Atelier does not interpret modes.
    """

    type: Literal["mode_change"] = "mode_change"
    ts: datetime
    mode_id: str


@dataclass(frozen=True, kw_only=True)
class SessionConfigOptions:
    """Provider-advertised mutable session options.

    ACP agents can expose per-session knobs such as OpenCode's model
    selector. Atelier records the advertised shape so the UI can render a
    real control and rebuild it from transcript replay after reconnects.
    The payload stays deliberately dict-shaped because option metadata is
    provider-owned and varies across ACP servers.
    """

    type: Literal["session_config_options"] = "session_config_options"
    ts: datetime
    options: tuple[dict[str, Any], ...]


@dataclass(frozen=True, kw_only=True)
class SessionConfigChanged:
    """A mutable provider session option changed successfully."""

    type: Literal["session_config_changed"] = "session_config_changed"
    ts: datetime
    config_id: str
    value: str | bool


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


@dataclass(frozen=True, kw_only=True)
class HandoffOffered:
    """The agent's reply indicates a provider-side auto-handoff to a new
    thread/session — currently emitted only by the Amp adapter, which
    pattern-matches the CLI's "Handoff created — work continues in
    T-XXX" assistant text. The original SDK stream typically ends with
    that message, so we surface this event so the UI can offer the user
    a one-click switch to continue in ``new_thread_id``. The switch
    rebuilds the adapter with ``continue_thread=new_thread_id``.
    """

    type: Literal["handoff_offered"] = "handoff_offered"
    ts: datetime
    new_thread_id: str


@dataclass(frozen=True, kw_only=True)
class ProviderContextCompacted:
    """Provider runtime compacted its own prompt context automatically.

    This is distinct from Atelier's explicit ``context_compacted`` transcript
    marker, which is written by the manual compact command and points at an
    Atelier-owned summary file. Provider-side auto compaction has no local
    summary artifact and may not replace the persisted provider session id.
    """

    type: Literal["provider_context_compacted"] = "provider_context_compacted"
    ts: datetime
    provider: str
    reason: str = "auto"


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
    # Optional ACP enrichment: the agent-provided answer options
    # (``{option_id, name, kind}`` with kind ∈ allow_once | allow_always |
    # reject_once | reject_always) and the tool call this request gates,
    # so the frontend can anchor the prompt to its tool card and label
    # the buttons with the agent's own wording. ``None`` from adapters
    # without this granularity; serializer omits.
    options: tuple[dict[str, Any], ...] | None = None
    tool_id: str | None = None


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
      for that call — the "should I /clear?" number. Consumers use it
      for **context %** display. Some providers can compact their own
      prompt context automatically, so this snapshot can drop without
      an Atelier manual compaction event.

      Why not sum ``input + cache_read + cache_creation``? Each sub-call
      replays the full history (read from cache), so summing across N
      sub-calls multiplies the same content N times. ``last_prompt_tokens``
      is a one-frame snapshot taken at end-of-turn, after every sub-call
      has fired.

    - ``context_window`` is optional runtime metadata from the provider.
      Static provider descriptors publish best-effort model windows, but
      some CLIs reserve part of the API window. When present, consumers
      should prefer this value for context percentage.

    - ``cost_usd`` is the provider-reported **cumulative session cost**
      (ACP ``usage_update.cost``), when the agent reports one. It is
      authoritative — consumers should prefer it over token-math price
      estimates. ``None`` from adapters/providers that don't report cost.
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
    context_window: int | None = None
    cost_usd: float | None = None
    git_branch: str | None = None
    git_head: str | None = None
    git_detached: bool | None = None


AgentEvent = (
    MessageDelta
    | MessageComplete
    | ThinkingDelta
    | ThinkingComplete
    | ToolCall
    | ToolCallUpdate
    | ToolResult
    | PlanUpdate
    | ModeChange
    | SessionConfigOptions
    | SessionConfigChanged
    | StatusChange
    | ArtifactMarker
    | Error
    | SessionEstablished
    | HandoffOffered
    | ProviderContextCompacted
    | PermissionRequest
    | PermissionDecision
    | TurnMetrics
)


__all__ = [
    "AgentEvent",
    "ArtifactMarker",
    "Error",
    "HandoffOffered",
    "MessageComplete",
    "MessageDelta",
    "ModeChange",
    "PermissionDecision",
    "PermissionDecisionValue",
    "PermissionRequest",
    "PlanUpdate",
    "ProviderContextCompacted",
    "SessionConfigChanged",
    "SessionConfigOptions",
    "SessionEstablished",
    "StatusChange",
    "ThinkingComplete",
    "ThinkingDelta",
    "ToolCall",
    "ToolCallUpdate",
    "ToolResult",
    "TurnMetrics",
]
