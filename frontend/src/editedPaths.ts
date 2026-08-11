import type { AgentEvent } from "./useAgentStream";

/** Canonical tool names that write to a file.
 *
 *  `infrastructure/agents/tool_canonical.canonicalize_tool` normalises every
 *  provider's tools to these concepts before the event is published, so this
 *  list is provider-independent. ACP runtimes also set `kind: "edit"`, which
 *  catches edit-shaped tools that have no canonical concept.
 */
const EDIT_TOOLS = new Set(["Edit", "MultiEdit", "Write", "NotebookEdit"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** The path a completed edit reports on its result.
 *
 *  The most reliable source, and for some providers the only one. A real ACP
 *  edit arrives as `name: "Editing files"` with `arguments: {}` and no
 *  `locations`; the file it touched is named only on the result, as
 *  `diff.path`. Reading the call alone means never seeing an edit at all.
 */
export function editedPathInResult(event: AgentEvent): string {
  const diff = isRecord(event.diff) ? event.diff : null;
  return diff ? text(diff.path) : "";
}

/** Every path one tool call writes to, absolute as the agent reported them. */
export function editedPathsInCall(event: AgentEvent): string[] {
  const name = text(event.name);
  const kind = text(event.kind);
  if (!EDIT_TOOLS.has(name) && kind !== "edit") return [];
  const paths: string[] = [];
  const args = isRecord(event.arguments) ? event.arguments : {};
  const direct = text(args.path);
  if (direct) paths.push(direct);
  // ACP enrichment: the tool may report the files it touches even when its
  // arguments do not name them in the canonical shape.
  if (Array.isArray(event.locations)) {
    for (const location of event.locations) {
      const path = isRecord(location) ? text(location.path) : "";
      if (path) paths.push(path);
    }
  }
  return Array.from(new Set(paths));
}

/** Paths whose writes have completed, for results newer than `sinceSeq`.
 *
 *  Keyed on `tool_result`, not `tool_call`: the call is emitted when the agent
 *  asks, and the bytes land when the tool returns. Reacting to the call reads
 *  the file before the write.
 *
 *  `sinceSeq` filters the *results*, never the input. A live stream delivers
 *  the call in one batch and its result in a later one, so indexing calls from
 *  a pre-filtered slice loses the path and the caller silently sees nothing --
 *  the whole event list has to be scanned for calls even when only the newest
 *  results are of interest.
 *
 *  Returns the highest result seq it handled so the caller can advance its
 *  cursor without assuming anything about ordering.
 */
export function completedEditPaths(
  events: AgentEvent[],
  sinceSeq = 0,
): { paths: string[]; lastSeq: number } {
  const pathsByToolId = new Map<string, string[]>();
  const paths: string[] = [];
  let lastSeq = sinceSeq;
  for (const event of events) {
    const toolId = text(event.tool_id);
    if (event.type === "tool_call") {
      if (!toolId) continue;
      const called = editedPathsInCall(event);
      if (called.length > 0) pathsByToolId.set(toolId, called);
      continue;
    }
    if (event.type !== "tool_result") continue;
    if (event.seq <= sinceSeq) continue;
    if (event.seq > lastSeq) lastSeq = event.seq;
    // A failed tool wrote nothing worth reloading for.
    if (event.ok === false || event.is_error === true || text(event.error)) continue;
    // The result's own `diff.path` first: it is the only source some providers
    // give. The call's paths are the fallback, for tools that name the file up
    // front but report nothing on completion.
    const fromResult = editedPathInResult(event);
    if (fromResult) {
      paths.push(fromResult);
      continue;
    }
    paths.push(...(pathsByToolId.get(toolId) ?? []));
  }
  return { paths, lastSeq };
}

/** Turn an absolute edited path into one relative to `root`, or `null`.
 *
 *  Artifact paths are plan-root-relative while tool calls report absolute
 *  paths, so the two only compare after a prefix strip. Anything outside the
 *  root is not a plan document and is ignored rather than guessed at.
 */
export function relativeToRoot(path: string, root: string): string | null {
  if (!path || !root) return null;
  const base = root.endsWith("/") ? root : `${root}/`;
  if (!path.startsWith(base)) return null;
  const relative = path.slice(base.length);
  return relative ? relative : null;
}

/** Resolve a markdown link against the document containing it.
 *
 *  Plan documents link to each other with relative paths (`../intent.md`,
 *  `stories/story-002.md`). Left to the browser these resolve against the app
 *  URL, not the plan, so they navigate out of the route table and land on the
 *  home screen. Resolving them here against the linking document's own path is
 *  what makes them mean what their author meant.
 *
 *  Returns a plan-relative path, or `null` when the link is not a plan
 *  document reference: an absolute URL, a mail/anchor link, or a path that
 *  climbs above the plan root.
 */
export function resolvePlanLink(href: string, fromPath: string): string | null {
  const target = href.trim();
  if (!target || target.startsWith("#")) return null;
  // Anything with a scheme belongs to the browser, not the plan.
  if (/^[a-z][a-z0-9+.-]*:/i.test(target)) return null;
  if (target.startsWith("//")) return null;
  const [withoutFragment] = target.split("#");
  if (!withoutFragment) return null;
  const base = withoutFragment.startsWith("/")
    ? []
    : fromPath.split("/").slice(0, -1);
  const segments = [...base, ...withoutFragment.split("/")];
  const resolved: string[] = [];
  for (const segment of segments) {
    if (!segment || segment === ".") continue;
    if (segment === "..") {
      // Climbing past the root would name a file the plan does not own.
      if (resolved.length === 0) return null;
      resolved.pop();
      continue;
    }
    resolved.push(segment);
  }
  return resolved.length > 0 ? resolved.join("/") : null;
}
