# Agent hooks — reacting to what an agent does

**Status:** phase 1 in progress; phase 2 scoped, deliberately not started.

## 1. Why

A view that shows a file the agent is editing does not know when it changed.
Editing a story from the planning chat leaves the story on screen stale until
the user refreshes or switches away and back. The same gap is about to appear
in loop run setup, where an agent is meant to fill in the goal and brief.

Both want the same thing — *the agent did something, react to it* — but they
are not the same problem, and the difference decides the mechanism.

## 2. The rule

**If the runtime can observe it, observe it. Never ask the model.**

A model can forget to announce an edit, describe it differently on a second
run, or announce one it did not make. Where the runtime already knows, a
declared marker adds a second source of truth that can disagree with the
first, and no way to tell which is right.

Declared markers are for things only the model knows: a judgement, a summary,
a decision. Not for facts the tool call already carries.

| Hook | Mechanism | Phase |
|---|---|---|
| A file was modified | Observed: `tool_call` / `tool_result` | 1 |
| Loop goal, brief, review checks | Declared: structured output contract | 2 |

## 3. Phase 1 — file modification (observed)

Nothing needs to be invented. `domain/agents/events.py:60` already
canonicalises every provider's tools to one shape, so the edit concepts carry
a path directly:

```
Edit       path, old_text, new_text
MultiEdit  path, edits[]
Write      path, content
```

ACP runtimes additionally set `kind: "edit"` and `locations: [{path, line?}]`,
whose stated purpose is follow-the-agent UX. These events already reach the
frontend over the existing WS; `AgentTile` already parses `tool_call`.

**Fire on `tool_result`, not `tool_call`** — and read the path from the result.
The call is emitted when the agent asks; the bytes land when the tool returns,
so refreshing on the call reads the file before the write.

The path is on the result too, and for some providers *only* there. A real ACP
edit arrives as `name: "Editing files"`, `arguments: {}`, no `locations` — the
file it touched is named on the result as `diff.path`. An implementation that
reads the call alone compiles, passes a test that hands it both events in one
array, and never fires against a live stream. The call's `path` / `locations`
remain the fallback for tools that name the file up front.

**Scan the whole event list, filter the results.** A live stream delivers the
call in one batch and the result in a later one. Filtering the input by a
"seen" cursor before scanning orphans the result — its call is below the
cursor — so the cursor applies to results only.

**One subscriber per chat.** `domain/supervisor/service.py:493` — a second
subscribe replaces the slot and kicks the first. `WorkView` therefore *cannot*
open its own stream to the planning chat: it would fight the chat dock. The
signal is lifted out of the component that already subscribes (`ChatTile`)
through a callback, not by subscribing again. This is the main structural
constraint on the design.

**Path resolution.** Tool-call paths are absolute; artifact paths are
plan-root-relative. `PlanDetail.plan_artifacts_path` is the absolute plan root,
so the relative path is a prefix strip. An edit outside that root is ignored.

**Two known bounds.** A reload is guarded by a sequence number, because two
edits in quick succession issue two fetches and the earlier one resolving last
would write the older content back — the hash check cannot catch that, since
stale content differs from fresh content just as much as fresh differs from
old.

And the chat stream replays only its last 100 events on a fresh mount, so an
edit older than that is never announced. Left as is deliberately: the run
transcript docks opt into full replay because they are a record, but a planning
chat is not — a real one here is 8,000+ events and 4MB, and pulling all of it
into memory to catch an edit that old is a bad trade. Anything within the
replayed tail still fires, and the refetch it triggers is guarded by the hash,
so a redundant one costs nothing.

**Draft safety.** `WorkView` holds `planDraft` for in-progress manual edits.
The existing run poll only overwrites it when the user has not typed
(`WorkView.tsx:912`, `currentDraft === previous.content`). Auto-refresh reuses
that rule; a chat edit must never silently discard what the user was writing.
A refresh also moves `source_hash`, which is the `expected_hash` a later save
sends — so a stale editor would 409 rather than clobber.

## 4. Phase 2 — declared hooks (scoped, not started)

The pattern already exists for loop stages: `prompts.py:316` declares a
required `atelier_loop_step_report` JSON contract, `reports.py:19` extracts it,
and a malformed report gets a repair prompt rather than failing the turn. A
goal/brief hook is the same shape with a different schema.

Generalising it means a per-context hook registry, sitting beside `chats.role`
and `ChatPosture` — the seam that already decides what kind of chat this is.
Each hook is a mini-prompt plus a schema; the caller declares what it wants
back, and the result arrives as a new `AgentEvent` variant the frontend
subscribes to like any other.

**Why this is not started yet.** Enforcing structured output comes first. The
loop contract is reliable because it is *one* required output at a turn
boundary, validated, with a repair path. Several ad-hoc markers fired
mid-conversation is a materially harder problem: more prompt for the model to
hold, more chances to omit one, and no natural point to validate. Two concrete
blockers:

- `reports.py:19` anchors on the whole message being the JSON
  (`^\s*\{...\}\s*$`). A chat that talks *and* emits needs an embedded
  sentinel and a different extractor.
- There is no schema-enforcement layer. Providers differ in whether they can
  be constrained to a schema at all, so today's contract leans on prompt plus
  repair. That is acceptable for one output per turn and probably not for
  several.

Until structured output is enforceable, phase 2 would ship a mechanism whose
failure mode is silence — a hook that just does not fire, with nothing to
retry against.

## 5. Not in scope

- Reacting to reads. Only modifications move a view.
- Cross-work reactions. A hook fires within the work whose chat produced it.
- Retrofitting hooks onto the loop stage report, which already works.
