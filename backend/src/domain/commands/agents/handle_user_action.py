"""Dispatch a parsed ``UserAction`` to the supervisor.

Symmetric inbound counterpart to ``connect`` on the streaming side: the
WS endpoint translates a wire frame to a typed action and hands it
off here. Adding a new action type is a one-class addition in
``domain/agents/user_actions.py`` plus one ``case`` branch below — the
WS handler stays unchanged.

``SendInput`` may carry attached contexts (the user clicked
"+ Add context" before hitting Send). When present, this dispatcher
runs ``add_contexts.execute`` first so the new files land on disk, then
forwards the user's text to the supervisor with a one-line prepend
that points the SDK at what's new.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.domain.agents import (
    RefreshSessionConfigOptions,
    ResolvePermission,
    SendInput,
    SetSessionConfigOption,
    StopTurn,
    UserAction,
)
from src.domain.commands.agents import add_contexts
from src.domain.connections import ConnectionStore
from src.domain.workstore.ports import WorkStore

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


async def execute(
    supervisor: AgentSupervisorService,
    workstore: WorkStore,
    connection_store: ConnectionStore,
    agent_slug: str,
    action: UserAction,
) -> None:
    match action:
        case SendInput(text=text, contexts=contexts):
            if contexts:
                # Add-then-send is two domain ops but one user gesture —
                # if the fetch fails we don't want a partial state where
                # the message went out but the context didn't. The
                # add_contexts command itself already leaves state
                # untouched on fetch failure (see its tests); we just
                # need to NOT call send_input on the error path. The
                # exception propagates to the WS handler, which will
                # surface it as a transcript error event.
                result = await asyncio.to_thread(
                    add_contexts.execute,
                    workstore,
                    connection_store,
                    add_contexts.AddContextsRequest(
                        agent_slug=agent_slug, contexts=contexts
                    ),
                )
                prepended = _prepend_context_hint(
                    text, result.new_file_paths, result.index_path
                )
                await supervisor.send_input(agent_slug, prepended)
            else:
                await supervisor.send_input(agent_slug, text)
        case StopTurn():
            await supervisor.stop_turn(agent_slug)
        case ResolvePermission(request_id=request_id, decision=decision):
            await supervisor.resolve_permission(agent_slug, request_id, decision)
        case SetSessionConfigOption(config_id=config_id, value=value):
            await supervisor.set_config_option(agent_slug, config_id, value)
            if config_id == "model" and isinstance(value, str):
                await asyncio.to_thread(workstore.set_agent_model, agent_slug, value)
        case RefreshSessionConfigOptions(config_id=config_id):
            await supervisor.refresh_config_options(agent_slug, config_id)


def _prepend_context_hint(
    text: str, new_file_paths: tuple[str, ...], index_path: str | None
) -> str:
    """Build the auto-prepend that points the SDK at new context files
    before the user's message. Mirrors the start-time first-message
    pattern: tell the model where to look, let it Read on demand.

    Paths are absolute — relative paths would resolve against the
    adapter's cwd (the per-agent git worktree), which doesn't contain
    ``context.md`` (that lives in the agent's metadata dir under the
    workspace root)."""
    if not new_file_paths or index_path is None:
        return text
    listed = ", ".join(f"`{p}`" for p in new_file_paths)
    hint = (
        f"[Atelier appended new context: {listed} — re-read `{index_path}` "
        "for the updated index.]"
    )
    return f"{hint}\n\n{text}"


__all__ = ["execute"]
