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
  /**
   * Where the command came from — drives the picker's slot tag and its
   * origin chips. Null whenever the agent didn't say, which today is
   * every ACP wrapper we have probed: `AvailableCommand` carries only
   * name/description/input, and `_meta` comes back empty from opencode,
   * claude-acp and codex-acp alike. Rows fall back to a neutral tag and
   * the chips hide themselves (0-count chips are hidden by spec), so the
   * axis is ready the moment a provider starts reporting it.
   */
  origin: CommandOrigin | null;
};

export type CommandOrigin = "project" | "user" | "builtin";

export const COMMAND_ORIGIN_TAG: Record<CommandOrigin, string> = {
  project: "PRJ",
  user: "USR",
  builtin: "OC",
};

export const COMMAND_ORIGIN_LABEL: Record<CommandOrigin, string> = {
  project: "project",
  user: "user",
  builtin: "built-in",
};

function parseOrigin(raw: unknown): CommandOrigin | null {
  return raw === "project" || raw === "user" || raw === "builtin" ? raw : null;
}

/**
 * A plausible command name.
 *
 * No slashes, so a draft that is just an absolute path
 * (`/Users/me/notes.md`) or a URL path reads as ordinary text rather
 * than as a command the agent failed to advertise — the slash is the
 * discriminator, not the leading character. A `$` prefix is allowed
 * because codex-acp namespaces plugin commands that way
 * (`$ponytail:ponytail-audit`), and without it those cannot be searched.
 */
const COMMAND_NAME = /^\$?[A-Za-z0-9][A-Za-z0-9_.:-]*$/;

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
    origin: parseOrigin(item.origin),
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
