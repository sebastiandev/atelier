/**
 * The `/` command picker shared by both composers (Chat and AgentTile).
 *
 * Deliberately one component: two composers with differently-behaved
 * pickers would be worse than none. Keyboard handling lives in
 * `commandPickerKeyDown` so the host textarea keeps ownership of its own
 * key ordering (Esc must still fall through to stop-agent when closed).
 */
import type {
  Dispatch,
  KeyboardEvent as ReactKeyboardEvent,
  ReactNode,
  RefObject,
  SetStateAction,
} from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  activeCommandMention,
  filterSessionCommands,
  latestSessionCommands,
  unknownCommandName,
  type CommandMentionState,
  type SessionCommand,
} from "./sessionCommands";
import type { AgentEvent } from "./useAgentStream";

export function CommandPicker({
  matches,
  total,
  selectedIndex,
  onPick,
}: {
  matches: readonly SessionCommand[];
  total: number;
  selectedIndex: number;
  onPick: (command: SessionCommand) => void;
}) {
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    optionRefs.current.length = matches.length;
  }, [matches.length]);

  useEffect(() => {
    if (selectedIndex < 0) return;
    optionRefs.current[selectedIndex]?.scrollIntoView({ block: "nearest" });
  }, [matches.length, selectedIndex]);

  return (
    <div className="composer-plan-mentions composer-commands">
      <div className="composer-plan-mentions-head">
        <span className="composer-plan-mentions-title">Commands</span>
        <span className="composer-commands-count">
          {matches.length} of {total}
        </span>
      </div>
      <div
        className="composer-plan-mentions-list"
        role="listbox"
        aria-label="Agent commands"
      >
        {matches.length > 0 ? (
          matches.map((command, i) => (
            <button
              key={command.name}
              ref={(el) => {
                optionRefs.current[i] = el;
              }}
              type="button"
              role="option"
              aria-selected={i === selectedIndex}
              className={i === selectedIndex ? "active" : undefined}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => onPick(command)}
            >
              <span className="pm-ref-kind">/</span>
              <span className="cmd-ref-body">
                <span className="cmd-ref-name">
                  {command.name}
                  {command.hint && (
                    <span className="cmd-ref-hint"> {command.hint}</span>
                  )}
                </span>
                {command.description && (
                  <span className="cmd-ref-desc">{command.description}</span>
                )}
              </span>
            </button>
          ))
        ) : (
          <div className="composer-plan-mentions-empty">
            No command matches that search
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Handle one keystroke for an open picker.
 *
 * Returns true when the key was consumed, so the caller can bail before
 * its own Enter-sends / Esc-stops handling. Mirrors the `@` mention
 * precedence in Chat.tsx: the picker owns Esc only while it is open.
 */
export function commandPickerKeyDown(
  e: ReactKeyboardEvent<HTMLTextAreaElement>,
  {
    matches,
    selectedIndex,
    onMove,
    onPick,
    onDismiss,
  }: {
    matches: readonly SessionCommand[];
    selectedIndex: number;
    onMove: (next: (current: CommandMentionState) => CommandMentionState) => void;
    onPick: (command: SessionCommand) => void;
    onDismiss: () => void;
  },
): boolean {
  if (e.key === "Escape") {
    e.preventDefault();
    onDismiss();
    return true;
  }
  if (matches.length === 0) return false;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    onMove((current) => ({
      ...current,
      index: (current.index + 1) % matches.length,
    }));
    return true;
  }
  if (e.key === "ArrowUp") {
    e.preventDefault();
    onMove((current) => ({
      ...current,
      index: (current.index - 1 + matches.length) % matches.length,
    }));
    return true;
  }
  if (e.key === "Enter" || e.key === "Tab") {
    e.preventDefault();
    const picked = matches[selectedIndex];
    if (picked) onPick(picked);
    return true;
  }
  return false;
}


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

/**
 * Wires the `/` picker into one composer.
 *
 * Three composers need identical behaviour (the chat route, the chat
 * tile, the agent tile); a hook keeps them from drifting apart. Every
 * value derives from what the session advertised, so a provider that
 * advertises nothing gets `picker: null` and an inert keyboard path
 * without anyone naming a provider.
 */
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
  const [error, setError] = useState<string | null>(null);

  const commands = useMemo(() => latestSessionCommands(events), [events]);
  const matches = useMemo(
    () => (mention ? filterSessionCommands(commands, mention.query) : []),
    [commands, mention],
  );
  const selectedIndex =
    mention && matches.length > 0
      ? Math.min(mention.index, matches.length - 1)
      : -1;

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
    setDraft((current) => {
      const next =
        token + current.slice(Math.min(mention.end, current.length));
      return next;
    });
    setMention(null);
    setError(null);
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(token.length, token.length);
    });
  }

  function reset() {
    setMention(null);
    setError(null);
  }

  return {
    picker: mention ? (
      <CommandPicker
        matches={matches}
        total={commands.length}
        selectedIndex={selectedIndex}
        onPick={insert}
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
      commandPickerKeyDown(e, {
        matches,
        selectedIndex,
        onMove: (next) => setMention((current) => (current ? next(current) : current)),
        onPick: insert,
        onDismiss: () => setMention(null),
      }),
    rejectsUnknownCommand: (text: string) => {
      const unknown = unknownCommandName(text, commands);
      if (unknown === null) return false;
      setError(`/${unknown} isn't a command this agent knows.`);
      return true;
    },
    reset,
  };
}
