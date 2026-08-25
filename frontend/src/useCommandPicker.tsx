/**
 * Wires the `/` command picker into one composer.
 *
 * Three composers need identical behaviour (the chat route, the chat
 * tile — which is what planning chat and loop discuss render — and the
 * agent tile); a hook keeps them from drifting apart. Every value
 * derives from what the session advertised, so a provider that
 * advertises nothing gets `picker: null` and an inert keyboard path
 * without anyone naming a provider.
 */
import type {
  Dispatch,
  KeyboardEvent as ReactKeyboardEvent,
  ReactNode,
  RefObject,
  SetStateAction,
} from "react";
import { useMemo, useState } from "react";

import {
  ComposerPicker,
  markMatches,
  pickerKeyDown,
  type PickerItem,
} from "./ComposerPicker";
import { filterSessionCommands, searchTerms } from "./pickerSearch";
import {
  activeCommandMention,
  latestSessionCommands,
  unknownCommandName,
  COMMAND_ORIGIN_LABEL,
  COMMAND_ORIGIN_TAG,
  type CommandMentionState,
  type CommandOrigin,
  type SessionCommand,
} from "./sessionCommands";
import type { AgentEvent } from "./useAgentStream";

const ORIGINS: CommandOrigin[] = ["project", "user", "builtin"];

export type CommandPickerHandle = {
  /** Rendered picker, or null when no slash token is under the caret. */
  picker: ReactNode;
  /** Inline "unknown command" notice, or null. */
  error: ReactNode;
  /** Re-scan the draft after a change / caret move. */
  sync: (text: string, cursor: number | null | undefined) => void;
  /** True when the keystroke belonged to an open picker. */
  handleKeyDown: (e: ReactKeyboardEvent<HTMLTextAreaElement>) => boolean;
  /**
   * True when the draft names a command the agent never advertised — the
   * caller must not send. Sets the inline notice as a side effect.
   */
  rejectsUnknownCommand: (text: string) => boolean;
  /** Drop picker + notice (on send, or when the draft is replaced). */
  reset: () => void;
};

export function useCommandPicker({
  events,
  setDraft,
  textareaRef,
}: {
  events: AgentEvent[];
  setDraft: Dispatch<SetStateAction<string>>;
  textareaRef: RefObject<HTMLTextAreaElement>;
}): CommandPickerHandle {
  const [mention, setMention] = useState<CommandMentionState | null>(null);
  const [originFilter, setOriginFilter] = useState<CommandOrigin | "all">("all");
  const [error, setError] = useState<string | null>(null);

  const commands = useMemo(() => latestSessionCommands(events), [events]);
  const searched = useMemo(
    () => (mention ? filterSessionCommands(commands, mention.query) : []),
    [commands, mention],
  );
  // Chips narrow the already-searched set and their counts follow the
  // query; a chip at 0 hides itself, which is what collapses the row
  // entirely while no provider reports an origin.
  const filters = useMemo(() => {
    const counts = ORIGINS.map((origin) => ({
      id: origin as string,
      label: COMMAND_ORIGIN_LABEL[origin],
      count: searched.filter((command) => command.origin === origin).length,
    })).filter((chip) => chip.count > 0);
    if (counts.length === 0) return [];
    return [
      { id: "all", label: "all", count: searched.length },
      ...counts,
    ];
  }, [searched]);
  const activeFilter =
    filters.some((chip) => chip.id === originFilter) ? originFilter : "all";
  const matches = useMemo(
    () =>
      activeFilter === "all"
        ? searched
        : searched.filter((command) => command.origin === activeFilter),
    [activeFilter, searched],
  );
  const selectedIndex =
    mention && matches.length > 0
      ? Math.min(mention.index, matches.length - 1)
      : -1;

  const terms = useMemo(
    () => (mention ? searchTerms(mention.query) : []),
    [mention],
  );
  const items: PickerItem[] = useMemo(
    () =>
      matches.map((command) => ({
        id: command.name,
        tag: command.origin ? COMMAND_ORIGIN_TAG[command.origin] : "/",
        tone: command.origin === "builtin" ? ("info" as const) : undefined,
        primary: markMatches(`/${command.name}`, terms),
        secondary: markMatches(
          command.hint
            ? `${command.hint} — ${command.description}`
            : command.description,
          terms,
        ),
      })),
    [matches, terms],
  );

  function sync(text: string, cursor: number | null | undefined) {
    setError(null);
    if (commands.length === 0) {
      setMention(null);
      return;
    }
    const next = activeCommandMention(text, cursor ?? text.length);
    setMention((current) =>
      next === null
        ? null
        : {
            ...next,
            index: current && current.query === next.query ? current.index : 0,
          },
    );
  }

  function insert(command: SessionCommand) {
    if (!mention) return;
    // Splice over the slash token only. The picker can be open with text
    // already in the draft (type a message, press Home, type "/"), and
    // replacing the whole value there would silently destroy it.
    const token = `/${command.name} `;
    setDraft(
      (current) => token + current.slice(Math.min(mention.end, current.length)),
    );
    setMention(null);
    setError(null);
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(token.length, token.length);
    });
  }

  return {
    picker: mention ? (
      <ComposerPicker
        trigger="slash"
        title="commands"
        items={items}
        filters={filters}
        activeFilter={activeFilter}
        onFilter={(id) => {
          setOriginFilter(id === "all" ? "all" : (id as CommandOrigin));
          setMention((current) => (current ? { ...current, index: 0 } : current));
        }}
        selectedIndex={selectedIndex}
        onPick={(name) => {
          const picked = matches.find((command) => command.name === name);
          if (picked) insert(picked);
        }}
        footerVerb="insert"
        total={commands.length}
        emptyLabel="no matching commands"
        listLabel="Agent commands"
      />
    ) : null,
    error: error ? (
      <div className="composer-command-error" role="alert">
        {error}
      </div>
    ) : null,
    sync,
    handleKeyDown: (e) =>
      mention !== null &&
      pickerKeyDown(e, {
        count: matches.length,
        selectedIndex,
        onMove: (nextIndex) =>
          setMention((current) =>
            current ? { ...current, index: nextIndex } : current,
          ),
        onPick: (index) => {
          const picked = matches[index];
          if (picked) insert(picked);
        },
        onDismiss: () => setMention(null),
      }),
    rejectsUnknownCommand: (text: string) => {
      const unknown = unknownCommandName(text, commands);
      if (unknown === null) return false;
      setError(`/${unknown} isn't a command this agent knows.`);
      return true;
    },
    reset: () => {
      setMention(null);
      setError(null);
    },
  };
}
