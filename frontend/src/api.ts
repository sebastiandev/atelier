export type WorkStatus = "active" | "completed" | "archived";

export type WorkSummary = {
  slug: string;
  name: string;
  description: string;
  status: WorkStatus;
  created_at: string;
  // Absolute path to ``~/Atelier/works/<slug>/`` — Atelier's own metadata
  // tree. Shown in the WorkView header pill; clicking it triggers
  // ``revealWork`` to open the folder in the OS file browser.
  atelier_path: string;
  // Optional grouping. ``null`` is "loose work". Resolve to a Project
  // record via ``listProjects()``.
  project_slug: string | null;
  // Aggregated child counts for the workspace cards. Default to 0 when the
  // backend doesn't populate them (e.g. older payloads or freshly-created
  // works that have no children yet).
  agent_count: number;
  artifact_count: number;
};

export type ContextEntry = {
  type: string;
  value: string;
  conn_id: string | null;
};

export type WorkDetail = WorkSummary & {
  contexts: ContextEntry[];
};

export type CreateWorkPayload = {
  name: string;
  description: string;
  contexts?: ContextEntry[];
  // Omit (or pass null) to create loose work.
  project_slug?: string | null;
};

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    // FastAPI conventionally returns `{"detail": "..."}` for HTTPException.
    // Surface just the detail so dialogs/toasts show a readable message
    // instead of the JSON wrapper. Fall back to the raw body for
    // non-JSON or unexpected shapes.
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (typeof parsed?.detail === "string") detail = parsed.detail;
    } catch {
      // body wasn't JSON — leave detail as the raw text
    }
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export function listWorks(): Promise<WorkSummary[]> {
  return fetch("/api/works").then((r) => jsonOrThrow<WorkSummary[]>(r));
}

export function getWork(slug: string): Promise<WorkDetail> {
  return fetch(`/api/works/${slug}`).then((r) => jsonOrThrow<WorkDetail>(r));
}

export function createWork(payload: CreateWorkPayload): Promise<WorkDetail> {
  return fetch("/api/works", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, contexts: payload.contexts ?? [] }),
  }).then((r) => jsonOrThrow<WorkDetail>(r));
}

/**
 * Open the work's atelier folder in the OS file browser. The backend
 * shells out to `open` / `xdg-open` / `explorer` depending on platform.
 */
export function revealWork(slug: string): Promise<void> {
  return fetch(`/api/works/${slug}/reveal`, { method: "POST" }).then(async (r) => {
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      throw new Error(`${r.status} ${r.statusText}: ${body}`);
    }
  });
}

/**
 * List local branches in the git repo at ``path``. Returns ``[]`` for
 * non-git folders so the FE can render a "not a git repo" hint without
 * branching on errors. Most-recently-committed branch comes first.
 */
export function listGitBranches(path: string): Promise<string[]> {
  const qs = new URLSearchParams({ path });
  return fetch(`/api/git/branches?${qs}`).then(async (r) => {
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      throw new Error(`${r.status} ${r.statusText}: ${body}`);
    }
    const data = (await r.json()) as { branches: string[] };
    return data.branches;
  });
}

export type CompleteWorkResponse = {
  work_slug: string;
  // Number of agents on the work; the backend stopped each + removed each
  // worktree. Used by the FE for the success toast.
  agent_count: number;
};

/**
 * Mark a work as complete: backend stops running agents, removes their git
 * worktrees, flips the work's status to "completed". Transcripts and the
 * work folder are preserved.
 */
export function completeWork(slug: string): Promise<CompleteWorkResponse> {
  return fetch(`/api/works/${slug}/complete`, { method: "POST" }).then((r) =>
    jsonOrThrow<CompleteWorkResponse>(r),
  );
}

/**
 * Re-parent a work to a different project. Pass `null` to make the work
 * Loose (no project). 404 if the work is unknown; 422 if the target
 * project doesn't exist.
 */
export function moveWorkToProject(
  slug: string,
  projectSlug: string | null,
): Promise<WorkDetail> {
  return fetch(`/api/works/${slug}/project`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_slug: projectSlug }),
  }).then((r) => jsonOrThrow<WorkDetail>(r));
}

export type Persona = "architect" | "developer" | "product" | "ux" | "writer";
export type AgentStatus =
  | "idle"
  | "live"
  | "thinking"
  | "error"
  | "stopped"
  | "detached";

export type AgentSummary = {
  slug: string;
  work_slug: string;
  name: string;
  persona: Persona;
  role: string;
  provider: string;
  model: string;
  // Working directory the adapter spawns in — per-agent so a single Work
  // can span multiple repos.
  folder: string;
  status: AgentStatus;
  started_at: string;
  stopped_at: string | null;
  // The dir the adapter actually runs in — the per-agent git worktree
  // when provisioned, else the source folder. Surfaced on the tile so
  // the user can reveal it in their file browser.
  worktree_path: string;
};

export function listAgents(workSlug: string): Promise<AgentSummary[]> {
  return fetch(`/api/works/${workSlug}/agents`).then((r) =>
    jsonOrThrow<AgentSummary[]>(r),
  );
}

/**
 * Permanently remove an agent: stops the supervisor task, removes the
 * per-agent worktree, wipes the workspace dir (transcript, agent.json,
 * contexts) + DB row. The parent work and its siblings are untouched.
 */
export async function deleteAgent(agentSlug: string): Promise<void> {
  const r = await fetch(`/api/agents/${agentSlug}`, { method: "DELETE" });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`Delete failed (${r.status}): ${detail}`);
  }
}

export const PERSONA_GLYPH: Record<Persona, string> = {
  architect: "AR",
  developer: "DV",
  product: "PM",
  ux: "UX",
  writer: "TW",
};

export const PERSONAS: { id: Persona; name: string; role: string }[] = [
  { id: "architect", name: "Architect", role: "Systems & API design" },
  { id: "developer", name: "Developer", role: "Implements features" },
  { id: "product", name: "Product", role: "Spec & requirements" },
  { id: "ux", name: "UX Designer", role: "Flows, IA, copy" },
  { id: "writer", name: "Tech Writer", role: "Docs, READMEs, RFCs" },
];

export type ProviderField = {
  label: string;
  values: string[];
  default: string;
  value_labels?: string[] | null;
};

export type ProviderTextField = {
  label: string;
  default: string;
  placeholder?: string | null;
  hint?: string | null;
  visible_when?: [string, string] | null;
};

export type ModelMeta = {
  context_window: number | null;
  input_per_mtok: number | null;
  output_per_mtok: number | null;
  cache_read_per_mtok: number | null;
  cache_write_per_mtok: number | null;
};

export type ProviderDescriptor = {
  name: string;
  label: string;
  primary_field: ProviderField;
  options: Record<string, ProviderField>;
  text_options?: Record<string, ProviderTextField>;
  advanced_intro?: string | null;
  model_meta?: Record<string, ModelMeta>;
};

export type CreateAgentPayload = {
  name: string;
  persona: Persona;
  role: string;
  provider: string;
  model: string;
  folder: string;
  options?: Record<string, string>;
  contexts?: ContextEntry[];
  // When set, fork the worktree from this existing agent in the same
  // work — new agent inherits source's uncommitted state in detached
  // HEAD. Used by the handoff flow.
  fork_from_agent?: string | null;
  // Optional branch name. Blank/null leaves the worktree in detached
  // HEAD (default); the agent picks a branch name via `git switch -c`
  // when it's ready.
  branch_name?: string | null;
};

export function listProviders(): Promise<ProviderDescriptor[]> {
  return fetch("/api/providers").then((r) => jsonOrThrow<ProviderDescriptor[]>(r));
}

export type FolderEntry = {
  name: string;
  is_dir: boolean;
  is_hidden: boolean;
};

export type FolderListing = {
  path: string;
  parent: string | null;
  entries: FolderEntry[];
};

export function listFolder(
  path?: string | null,
  showHidden: boolean = false,
): Promise<FolderListing> {
  const params = new URLSearchParams();
  if (path) params.set("path", path);
  if (showHidden) params.set("show_hidden", "true");
  const qs = params.toString();
  return fetch(`/api/fs/list${qs ? `?${qs}` : ""}`).then((r) =>
    jsonOrThrow<FolderListing>(r),
  );
}

// ─── Shared folders ────────────────────────────────────────────────────────

export type SharedFolderSummary = {
  slug: string;
  name: string;
  mount_path: string;
  canonical_path: string;
  real_path: string | null;
  is_custom_location: boolean;
  created_at: string;
};

export type CreateNewSharedFolderPayload = {
  mode: "new";
  name: string;
  mount_path: string;
  /** Absolute path. Omitted/null → default location under the project dir. */
  location?: string | null;
};

export type CreateExistingSharedFolderPayload = {
  mode: "existing";
  name: string;
  mount_path: string;
  /** Absolute path of an existing folder to point Atelier at. */
  location: string;
};

export type CreateSharedFolderPayload =
  | CreateNewSharedFolderPayload
  | CreateExistingSharedFolderPayload;

export function listProjectShares(
  projectSlug: string,
): Promise<SharedFolderSummary[]> {
  return fetch(`/api/projects/${projectSlug}/shares`).then((r) =>
    jsonOrThrow<SharedFolderSummary[]>(r),
  );
}

export function createProjectShare(
  projectSlug: string,
  payload: CreateSharedFolderPayload,
): Promise<SharedFolderSummary> {
  return fetch(`/api/projects/${projectSlug}/shares`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<SharedFolderSummary>(r));
}

export function renameProjectShare(
  projectSlug: string,
  shareSlug: string,
  name: string,
): Promise<SharedFolderSummary> {
  return fetch(`/api/projects/${projectSlug}/shares/${shareSlug}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  }).then((r) => jsonOrThrow<SharedFolderSummary>(r));
}

/** Two flavours of removal:
 *  - ``deleteData=false`` (default) → "stop sharing": remove the
 *    Atelier-side registration + symlink, real folder untouched.
 *  - ``deleteData=true`` → additionally wipe canonical contents.
 *    Refused server-side for custom-location shares. */
export function deleteProjectShare(
  projectSlug: string,
  shareSlug: string,
  deleteData: boolean = false,
): Promise<void> {
  const qs = deleteData ? "?delete_data=true" : "";
  return fetch(`/api/projects/${projectSlug}/shares/${shareSlug}${qs}`, {
    method: "DELETE",
  }).then((r) => {
    if (!r.ok) throw new Error(`Delete failed: ${r.status}`);
  });
}

export function createAgent(
  workSlug: string,
  payload: CreateAgentPayload,
): Promise<AgentSummary> {
  return fetch(`/api/works/${workSlug}/agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, contexts: payload.contexts ?? [] }),
  }).then((r) => jsonOrThrow<AgentSummary>(r));
}

export type DetachAgentResult = {
  command: string;
  launched: boolean;
};

/**
 * Detach an agent from Atelier and hand it to the user's terminal CLI.
 *
 * The backend stops the supervisor's SDK process, flips the agent's
 * status to ``detached``, and shells out to the OS terminal with
 * ``claude --resume`` / ``amp threads continue``. If the shell-out
 * fails (no detected terminal emulator), ``launched`` is false and the
 * caller should copy ``command`` to the clipboard instead.
 */
export function detachAgent(agentSlug: string): Promise<DetachAgentResult> {
  return fetch(`/api/agents/${agentSlug}/detach`, { method: "POST" }).then((r) =>
    jsonOrThrow<DetachAgentResult>(r),
  );
}

/**
 * Open the agent's worktree (or source folder, if no worktree was
 * provisioned) in the OS file browser. Same shell-out pattern as
 * ``revealWork``.
 */
export function revealAgent(agentSlug: string): Promise<void> {
  return fetch(`/api/agents/${agentSlug}/reveal`, { method: "POST" }).then(
    async (r) => {
      if (!r.ok) {
        const body = await r.text().catch(() => "");
        throw new Error(`${r.status} ${r.statusText}: ${body}`);
      }
    },
  );
}

// ---------------------------------------------------------------------------
// Connections
// ---------------------------------------------------------------------------

export type ConnectionType = "jira" | "sentry" | "honeycomb";

// Per-type configs — discriminated union on `type`. Mirrors the backend
// dataclasses (JiraConfig / SentryConfig / HoneycombConfig).
export type JiraConfig = { type: "jira"; url: string; email: string };
export type SentryConfig = { type: "sentry"; org: string };
export type HoneycombConfig = { type: "honeycomb"; env: string; team: string | null };
export type ConnectionConfig = JiraConfig | SentryConfig | HoneycombConfig;

export type Connection = {
  slug: string;
  name: string;
  created_at: string;
  config: ConnectionConfig;
  verified: boolean;
  last_used: string | null;
};

// Convenience: every config has a `type` discriminator. Keeps callers
// from reaching into config when they only need the type tag.
export function connectionType(c: Connection): ConnectionType {
  return c.config.type;
}

export type NewConnectionPayload = {
  name: string;
  token: string;
  config: ConnectionConfig;
};

export type PatchConnectionPayload = {
  name?: string;
  token?: string;
  config?: ConnectionConfig;
};

export type VerifyResponse = {
  verified: boolean;
  error: string | null;
};

// Mirror of backend ConnectionField / ConnectionDescriptor. Drives the
// per-type form rendering.
export type ConnectionField = {
  id: string;
  label: string;
  placeholder: string | null;
  required: boolean;
  secret: boolean;
  options: string[] | null;
};

export type ConnectionDescriptor = {
  type: ConnectionType;
  label: string;
  glyph: string;
  docs: string;
  config_fields: ConnectionField[];
  verifiable: boolean;
  context_fetchable: boolean;
};

export function listConnectionTypes(): Promise<ConnectionDescriptor[]> {
  return fetch("/api/connections/types").then((r) =>
    jsonOrThrow<ConnectionDescriptor[]>(r),
  );
}

export function listConnections(): Promise<Connection[]> {
  return fetch("/api/connections").then((r) => jsonOrThrow<Connection[]>(r));
}

export function createConnection(payload: NewConnectionPayload): Promise<Connection> {
  return fetch("/api/connections", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<Connection>(r));
}

export function patchConnection(
  slug: string,
  payload: PatchConnectionPayload,
): Promise<Connection> {
  return fetch(`/api/connections/${slug}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<Connection>(r));
}

export function deleteConnection(slug: string): Promise<void> {
  return fetch(`/api/connections/${slug}`, { method: "DELETE" }).then((res) => {
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  });
}

export function verifyConnection(slug: string): Promise<VerifyResponse> {
  return fetch(`/api/connections/${slug}/verify`, { method: "POST" }).then((r) =>
    jsonOrThrow<VerifyResponse>(r),
  );
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

export type ProjectSummary = {
  slug: string;
  name: string;
  description: string;
  // 1–2 char monogram derived from name on create; user-overridable later.
  glyph: string;
  // OKLCH hue 0–360. CSS exposes via --proj-h on cards/chips so a single
  // hue tints background, glyph bg, soft wash, and border line.
  color: number;
  pinned: boolean;
  default_jira_conn: string | null;
  default_sentry_conn: string | null;
  created_at: string;
};

// Reserved for future fields specific to the detail view; today equals Summary.
export type ProjectDetail = ProjectSummary;

export type CreateProjectPayload = {
  name: string;
  description?: string;
  glyph: string;
  color: number;
  pinned?: boolean;
  default_jira_conn?: string | null;
  default_sentry_conn?: string | null;
};

/**
 * Partial update — every field is optional. Backend leaves untouched
 * fields alone (None = "don't change" semantics). To clear a default
 * connection today, pick a different one — clear-to-null is a follow-up.
 */
export type PatchProjectPayload = {
  name?: string;
  description?: string;
  glyph?: string;
  color?: number;
  pinned?: boolean;
  default_jira_conn?: string | null;
  default_sentry_conn?: string | null;
};

export function listProjects(): Promise<ProjectSummary[]> {
  return fetch("/api/projects").then((r) => jsonOrThrow<ProjectSummary[]>(r));
}

export function getProject(slug: string): Promise<ProjectDetail> {
  return fetch(`/api/projects/${slug}`).then((r) => jsonOrThrow<ProjectDetail>(r));
}

export function createProject(payload: CreateProjectPayload): Promise<ProjectDetail> {
  return fetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...payload,
      description: payload.description ?? "",
    }),
  }).then((r) => jsonOrThrow<ProjectDetail>(r));
}

export function patchProject(
  slug: string,
  payload: PatchProjectPayload,
): Promise<ProjectDetail> {
  return fetch(`/api/projects/${slug}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<ProjectDetail>(r));
}

/**
 * Delete a project. Attached works are demoted to "loose" (project_slug
 * → null) by the backend's ON DELETE SET NULL FK rule. The project's
 * filesystem dir is removed best-effort.
 */
export function deleteProject(slug: string): Promise<void> {
  return fetch(`/api/projects/${slug}`, { method: "DELETE" }).then(async (r) => {
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      throw new Error(`${r.status} ${r.statusText}: ${body}`);
    }
  });
}

// ---------------------------------------------------------------------------
// Artifacts
// ---------------------------------------------------------------------------

export type ArtifactType = "pr" | "jira" | "doc";

export type ArtifactLocation = "worktree" | "shared";
export type ArtifactGitState = "committed" | "uncommitted";

export type ArtifactSummary = {
  slug: string;
  type: ArtifactType;
  title: string;
  // Per-type enum from the backend tracker — pr: open|draft|merged|closed,
  // jira: todo|in_progress|in_review|done|blocked, doc: draft|published.
  // For doc rows the FE now ignores this in favour of the location +
  // git_state chips below; the field stays in the schema for PR/Jira.
  status: string;
  created_at: string;
  // The agent that emitted the marker, if attribution was supplied.
  agent_slug: string | null;
  url: string | null;
  repo: string | null;
  // Absolute path on disk, for doc-type artifacts; click → revealArtifact.
  doc_path: string | null;
  // Doc-only enrichments computed on each list call. ``null`` for
  // PR/Jira and for stale doc rows whose path no longer resolves.
  location_kind: ArtifactLocation | null;
  git_state: ArtifactGitState | null;
};

export function listArtifacts(workSlug: string): Promise<ArtifactSummary[]> {
  return fetch(`/api/works/${workSlug}/artifacts`).then((r) =>
    jsonOrThrow<ArtifactSummary[]>(r),
  );
}

/**
 * Open a doc-type artifact's underlying file in the OS file browser.
 * 404 if the slug is unknown; 422 if the artifact isn't a doc.
 */
export function revealArtifact(slug: string): Promise<void> {
  return fetch(`/api/artifacts/${slug}/reveal`, { method: "POST" }).then(
    async (r) => {
      if (!r.ok) {
        const body = await r.text().catch(() => "");
        throw new Error(`${r.status} ${r.statusText}: ${body}`);
      }
    },
  );
}

// ---------------------------------------------------------------------------
// Handoffs
// ---------------------------------------------------------------------------

export type HandoffSummary = {
  slug: string;
  source_agent_slug: string;
  doc_path: string;
  // Markdown body — pre-fetched so the FE can pre-fill the NewAgentDialog
  // without a follow-up GET.
  doc_text: string;
  created_at: string;
  target_agent_slug: string | null;
  target_dialog: "new-agent" | null;
};

/**
 * Generate a handoff doc summarizing the source agent's recent transcript.
 * Synchronous: the request blocks for the duration of the LLM call (a
 * few seconds typically; 60s timeout). The returned summary's doc_text
 * is what the FE pre-fills the NewAgentDialog with.
 */
export function createHandoff(
  workSlug: string,
  sourceAgentSlug: string,
): Promise<HandoffSummary> {
  return fetch(`/api/works/${workSlug}/handoffs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_agent_slug: sourceAgentSlug }),
  }).then((r) => jsonOrThrow<HandoffSummary>(r));
}

/**
 * Derive a 1–2 character monogram from a project name. Used by the New
 * Project dialog to seed the glyph field — user can override before save.
 * "Acme Web" → "AW", "Platform" → "PL", "design-system" → "DS".
 */
export function deriveGlyph(name: string): string {
  const words = name
    .trim()
    .split(/[\s\-_/]+/)
    .filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) {
    const w = words[0];
    return (w.length >= 2 ? w.slice(0, 2) : w).toUpperCase();
  }
  return (words[0][0] + words[1][0]).toUpperCase();
}
