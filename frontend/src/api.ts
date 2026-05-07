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
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
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

export type ProviderDescriptor = {
  name: string;
  label: string;
  primary_field: ProviderField;
  options: Record<string, ProviderField>;
  text_options?: Record<string, ProviderTextField>;
  advanced_intro?: string | null;
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
};

export function listProviders(): Promise<ProviderDescriptor[]> {
  return fetch("/api/providers").then((r) => jsonOrThrow<ProviderDescriptor[]>(r));
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
