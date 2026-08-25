/**
 * Matching and ordering for the ⌘K picker.
 *
 * Split out of `SearchModal` so it can be unit-tested directly — the
 * component itself is not testable under `node --test`. Matching stays
 * the same lowercase substring rule the modal has always used; the new
 * part is recency, which is the *no-query* default ordering rather than
 * a ranking function (once a term is typed, match order stands).
 */
import type { RecentRun, WorkSummary } from "./api";

/** Does this run match the typed term? Empty term matches everything. */
export function runMatches(run: RecentRun, term: string): boolean {
  const needle = term.trim().toLowerCase();
  if (!needle) return true;
  return [
    run.goal,
    run.source_ref,
    run.source_title,
    run.work_slug,
    run.work_name,
  ].some((field) => field != null && field.toLowerCase().includes(needle));
}

/**
 * Runs newest-updated first.
 *
 * The endpoint already returns this order; re-sorting client-side keeps
 * the guarantee local rather than resting on a server contract the
 * component cannot see.
 */
export function byRecentRun(a: RecentRun, b: RecentRun): number {
  return b.updated_at.localeCompare(a.updated_at);
}

/**
 * Works newest-created first.
 *
 * `created_at` is the only timestamp on `WorkSummary`, so this is
 * "recently created", not "recently touched". Sorting on activity would
 * need a new field on the summary and is deliberately out of scope.
 */
export function byRecentWork(a: WorkSummary, b: WorkSummary): number {
  return b.created_at.localeCompare(a.created_at);
}

/** A `?run=` deep link, with the artifact that resolves it when known. */
export type RunDeepLink = {
  runId: string;
  /** Plan artifact the run belongs to; absent for a Loop-mode run. */
  artifactId: string | null;
};

/**
 * Parse `?run=` (and its optional `?artifact=`) off a query string.
 *
 * Returns null when absent or blank so the caller falls through to the
 * work overview rather than rendering an error from a stale bookmark.
 * The artifact is a resolution shortcut, not a requirement: it tells
 * the caller the run is a planning run and which story to open.
 */
export function runDeepLinkFrom(search: string): RunDeepLink | null {
  const params = new URLSearchParams(search);
  const runId = params.get("run");
  if (!runId || !runId.trim()) return null;
  const artifactId = params.get("artifact");
  return {
    runId,
    artifactId: artifactId && artifactId.trim() ? artifactId : null,
  };
}
