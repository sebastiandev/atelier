/**
 * Shared free-text search for both composer pickers.
 *
 * The query lives in the textarea (never a field inside the popover);
 * terms split on `/`, `-` and whitespace and **every term must hit**.
 * Matched runs get the accent mark, in both lines, same as ⌘K search.
 *
 * Design: `design/Composer Picker Handoff.md` §03.
 */
import type { SessionCommand } from "./sessionCommands";

export function searchTerms(query: string): string[] {
  return query
    .trim()
    .toLowerCase()
    .split(/[/\s-]+/)
    .filter(Boolean);
}

/** True when every term appears somewhere in `haystack`. */
export function matchesAllTerms(haystack: string, terms: string[]): boolean {
  const text = haystack.toLowerCase();
  return terms.every((term) => text.includes(term));
}

export type MatchSegment = {
  text: string;
  marked: boolean;
};

/**
 * `text` split into marked / unmarked runs.
 *
 * Overlapping and repeated hits collapse into one segment per covered
 * run, so "test" over "flaky-tests" yields a single marked span rather
 * than nested ones. Pure string work on purpose: the rendering half
 * lives in ComposerPicker so this stays unit-testable.
 */
export function splitMatches(text: string, terms: string[]): MatchSegment[] {
  if (terms.length === 0 || !text) return [{ text, marked: false }];
  const lower = text.toLowerCase();
  const covered = new Array<boolean>(text.length).fill(false);
  for (const term of terms) {
    if (!term) continue;
    let from = lower.indexOf(term);
    while (from !== -1) {
      for (let i = from; i < from + term.length; i += 1) covered[i] = true;
      from = lower.indexOf(term, from + 1);
    }
  }
  const segments: MatchSegment[] = [];
  let run = "";
  let marked = covered[0] ?? false;
  for (let i = 0; i < text.length; i += 1) {
    if (covered[i] !== marked) {
      segments.push({ text: run, marked });
      run = "";
      marked = covered[i];
    }
    run += text[i];
  }
  if (run) segments.push({ text: run, marked });
  return segments;
}

/**
 * Commands matching `query`, in advertised order.
 *
 *
 * Spec §03: terms split on `/`, `-` and whitespace, every term must
 * hit, and commands match on name + description — the same rule
 * `filterPlanReferences` already applies to `@`.
 */
export function filterSessionCommands(
  commands: readonly SessionCommand[],
  query: string,
): SessionCommand[] {
  const terms = searchTerms(query);
  if (terms.length === 0) return [...commands];
  return commands.filter((command) =>
    matchesAllTerms(`${command.name} ${command.description}`, terms),
  );
}

