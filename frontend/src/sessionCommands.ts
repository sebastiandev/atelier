/**
 * Provider-advertised slash commands.
 *
 * ACP agents announce their command set over `available_commands_update`;
 * the backend folds that into a sticky `session_commands` event. Nothing
 * here is provider-aware on purpose: a session that advertises commands
 * gets a picker, one that doesn't gets nothing, and neither branch names
 * OpenCode. See docs/frontend.md.
 */
import type { AgentEvent } from "./useAgentStream";

export type SessionCommand = {
  name: string;
  description: string;
  hint: string | null;
};

/**
 * A plausible command name: no slashes, so a draft that is just an
 * absolute path (`/Users/me/notes.md`) or a URL path reads as ordinary
 * text rather than as a command the agent failed to advertise.
 */
const COMMAND_NAME = /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/;

export type CommandMentionState = {
  start: number;
  end: number;
  query: string;
  index: number;
};

function parseCommand(raw: unknown): SessionCommand | null {
  if (!raw || typeof raw !== "object") return null;
  const item = raw as Record<string, unknown>;
  const name = typeof item.name === "string" ? item.name : "";
  if (!name) return null;
  return {
    name,
    description: typeof item.description === "string" ? item.description : "",
    hint: typeof item.hint === "string" && item.hint ? item.hint : null,
  };
}

/** The command set as of the newest `session_commands` event. */
export function latestSessionCommands(events: AgentEvent[]): SessionCommand[] {
  let commands: SessionCommand[] = [];
  for (const event of events) {
    if (event.type !== "session_commands" || !Array.isArray(event.commands)) {
      continue;
    }
    commands = event.commands
      .map(parseCommand)
      .filter((command): command is SessionCommand => command !== null);
  }
  return commands;
}

/**
 * The slash token under the caret, or null.
 *
 * Stricter than the `@` mention scan: a command is only a command as the
 * very first thing in the draft, so `src/foo` and `and/or` never trigger
 * it. Once a space is typed the name is settled and the picker closes —
 * what follows are the command's arguments.
 */
export function activeCommandMention(
  text: string,
  cursor: number,
): CommandMentionState | null {
  if (!text.startsWith("/")) return null;
  const before = text.slice(0, cursor);
  if (before.indexOf("/") !== 0) return null;
  const query = before.slice(1);
  if (/\s/.test(query)) return null;
  if (query !== "" && !COMMAND_NAME.test(query)) return null;
  return { start: 0, end: cursor, query, index: 0 };
}

/**
 * Rank of `command` against `term`, or null when it doesn't match.
 * Lower sorts first. Name beats description so `/rev` puts `review`
 * above a command that merely mentions "review" in its blurb.
 */
function commandRank(command: SessionCommand, term: string): number | null {
  const name = command.name.toLowerCase();
  if (name.startsWith(term)) return 0;
  if (name.includes(term)) return 1;
  if (isSubsequence(term, name)) return 2;
  if (command.description.toLowerCase().includes(term)) return 3;
  return null;
}

/** True when every char of `term` appears in `text`, in order. */
function isSubsequence(term: string, text: string): boolean {
  if (term.length === 0) return true;
  let i = 0;
  for (const char of text) {
    if (char === term[i]) i += 1;
    if (i === term.length) return true;
  }
  return false;
}

/**
 * Commands matching `query`, best match first.
 *
 * Searches names *and* descriptions, and tolerates gaps in the name
 * (`/cmp` finds `compact`) — the advertised set runs long once a project
 * has its own commands, and an exact-prefix-only filter makes a command
 * you only half-remember unfindable.
 */
export function filterSessionCommands(
  commands: readonly SessionCommand[],
  query: string,
): SessionCommand[] {
  const term = query.trim().toLowerCase();
  if (!term) return [...commands];
  return commands
    .map((command) => ({ command, rank: commandRank(command, term) }))
    .filter(
      (entry): entry is { command: SessionCommand; rank: number } =>
        entry.rank !== null,
    )
    .sort(
      (a, b) => a.rank - b.rank || a.command.name.localeCompare(b.command.name),
    )
    .map((entry) => entry.command);
}

/**
 * The command name in a draft that the agent has not advertised, or null.
 *
 * Providers parse the leading slash themselves and silently no-op a name
 * they don't know — an end_turn with no output, indistinguishable from a
 * hung agent. Callers use this to refuse the send instead. Returns null
 * when the session advertises nothing at all, so a provider without
 * command support keeps sending slash-prefixed text verbatim.
 */
export function unknownCommandName(
  text: string,
  commands: readonly SessionCommand[],
): string | null {
  if (commands.length === 0) return null;
  const body = text.trim();
  if (!body.startsWith("/")) return null;
  const name = body.slice(1).split(/\s/, 1)[0] ?? "";
  if (!name || !COMMAND_NAME.test(name)) return null;
  return commands.some((command) => command.name === name) ? null : name;
}
