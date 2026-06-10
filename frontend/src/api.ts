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
  from_chat: WorkChatRef | null;
};

export type WorkChatRef = {
  slug: string;
  title: string;
};

export type WorkChatContextFolder = {
  name: string;
  mount_path: string;
  chat_slug: string;
  chat_title: string;
  context_filename: string;
  absolute_path: string;
};

export type ContextEntry = {
  type: string;
  value: string;
  conn_id: string | null;
};

export type WorkDetail = WorkSummary & {
  contexts: ContextEntry[];
  chat_context_folders: WorkChatContextFolder[];
};

export type CreateWorkPayload = {
  name: string;
  description: string;
  contexts?: ContextEntry[];
  // Omit (or pass null) to create loose work.
  project_slug?: string | null;
  from_chat?: WorkChatRef | null;
  chat_context_folders?: CreateWorkChatContextFolder[];
};

export type CreateWorkChatContextFolder = {
  name: string;
  mount_path: string;
  chat_slug: string;
  chat_title: string;
  context_markdown: string;
  context_filename?: string;
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

export type UpdateStatus = {
  available: boolean;
  repo_path: string;
  current_sha: string | null;
  latest_sha: string | null;
};

export function getUpdateStatus(): Promise<UpdateStatus> {
  return fetch("/api/update-status").then((r) => jsonOrThrow<UpdateStatus>(r));
}

export function getWork(slug: string): Promise<WorkDetail> {
  return fetch(`/api/works/${slug}`).then((r) => jsonOrThrow<WorkDetail>(r));
}

export function createWork(payload: CreateWorkPayload): Promise<WorkDetail> {
  return fetch("/api/works", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...payload,
      contexts: payload.contexts ?? [],
      chat_context_folders: payload.chat_context_folders ?? [],
    }),
  }).then((r) => jsonOrThrow<WorkDetail>(r));
}

export function patchWork(
  slug: string,
  payload: Partial<Pick<WorkDetail, "name" | "description" | "status" | "contexts">>,
): Promise<WorkDetail> {
  return fetch(`/api/works/${slug}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<WorkDetail>(r));
}

// ─── Chats ────────────────────────────────────────────────────────────────

export type ChatGrounding =
  | { kind: "project"; ref: string }
  | { kind: "work"; ref: string }
  | { kind: "folder"; ref: string };

export type ChatMessage = {
  role: "user" | "assistant";
  body: string;
  created_at: string;
};

export type ChatSummary = {
  slug: string;
  title: string;
  provider: string;
  model: string;
  grounding: ChatGrounding | null;
  working_directory: string | null;
  created_at: string;
  updated_at: string;
  promoted_to_work_slug: string | null;
  message_count: number;
};

export type ChatDetail = ChatSummary & {
  transcript: ChatMessage[];
};

export type CreateChatPayload = {
  provider: string;
  model: string;
  first_message: string;
  title?: string | null;
  grounding?: ChatGrounding | null;
  working_directory?: string | null;
};

export function listChats(scope?: {
  project_slug?: string;
  work_slug?: string;
}): Promise<ChatSummary[]> {
  const params = new URLSearchParams();
  if (scope?.project_slug) params.set("project_slug", scope.project_slug);
  if (scope?.work_slug) params.set("work_slug", scope.work_slug);
  const qs = params.toString();
  return fetch(`/api/chats${qs ? `?${qs}` : ""}`).then((r) =>
    jsonOrThrow<ChatSummary[]>(r),
  );
}

export function createChat(payload: CreateChatPayload): Promise<ChatDetail> {
  return fetch("/api/chats", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<ChatDetail>(r));
}

export function getChat(slug: string): Promise<ChatDetail> {
  return fetch(`/api/chats/${slug}`).then((r) => jsonOrThrow<ChatDetail>(r));
}

export function patchChat(
  slug: string,
  patch: { title?: string },
): Promise<ChatDetail> {
  return fetch(`/api/chats/${slug}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }).then((r) => jsonOrThrow<ChatDetail>(r));
}

export async function deleteChat(slug: string): Promise<void> {
  const r = await fetch(`/api/chats/${slug}`, { method: "DELETE" });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`Delete failed (${r.status}): ${detail}`);
  }
}

export function sendChatMessage(slug: string, body: string): Promise<ChatDetail> {
  return fetch(`/api/chats/${slug}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  }).then((r) => jsonOrThrow<ChatDetail>(r));
}

export function promoteChat(
  slug: string,
  payload: { name: string; description: string; project_slug?: string | null },
): Promise<WorkDetail> {
  return fetch(`/api/chats/${slug}/promote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<WorkDetail>(r));
}

export type WorkChatContextDoc = {
  path: string;
  content: string;
};

export function getWorkChatContextDoc(
  workSlug: string,
  folderName: string,
  filename: string,
): Promise<WorkChatContextDoc> {
  return fetch(
    `/api/works/${workSlug}/chat-contexts/${encodeURIComponent(folderName)}/${encodeURIComponent(filename)}`,
  ).then((r) => jsonOrThrow<WorkChatContextDoc>(r));
}

export function ensureWorkChatContext(
  workSlug: string,
  chatSlug: string,
): Promise<WorkChatContextFolder> {
  return fetch(
    `/api/works/${workSlug}/chats/${encodeURIComponent(chatSlug)}/context`,
    { method: "POST" },
  ).then((r) => jsonOrThrow<WorkChatContextFolder>(r));
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

/** Partial update for an agent. Today only ``name`` is mutable; other
 *  fields are FS-canonical and set at create time. */
export async function patchAgent(
  agentSlug: string,
  patch: { name?: string },
): Promise<AgentSummary> {
  const r = await fetch(`/api/agents/${agentSlug}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`Patch failed (${r.status}): ${detail}`);
  }
  return jsonOrThrow<AgentSummary>(r);
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
  effort_values?: string[] | null;
  effort_default?: string | null;
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
export function detachAgent(
  agentSlug: string,
  kind?: string,
): Promise<DetachAgentResult> {
  const qs = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  return fetch(`/api/agents/${agentSlug}/detach${qs}`, { method: "POST" }).then(
    (r) => jsonOrThrow<DetachAgentResult>(r),
  );
}

/**
 * Switch an agent's underlying provider thread to ``threadId`` — used
 * to recover from Amp's auto-handoff where the SDK stream ends with
 * "work continues in T-…". The backend stops the current adapter,
 * persists the new ``session_id``, writes a ``handoff_accepted``
 * transcript marker, and re-registers the agent lazily so the next
 * user input spawns a fresh CLI subprocess against the new thread.
 */
export function switchAgentThread(
  agentSlug: string,
  threadId: string,
): Promise<void> {
  return fetch(`/api/agents/${agentSlug}/switch-thread`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId }),
  }).then(async (r) => {
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      throw new Error(`${r.status} ${r.statusText}: ${body}`);
    }
  });
}

export type CompactAgentReason = "manual" | "forced_context_limit";

export type CompactAgentResponse = {
  agent_slug: string;
  work_slug: string;
  provider: string;
  old_session_id: string;
  new_session_id: string;
  summary_path: string;
  breadcrumb_written: boolean;
  breadcrumb_error: string | null;
};

export type AgentCompactionSummary = {
  agent_slug: string;
  work_slug: string;
  filename: string;
  summary_path: string;
  content: string;
};

export type CompactChatResponse = {
  chat_slug: string;
  provider: string;
  old_session_id: string;
  new_session_id: string;
  summary_path: string;
  breadcrumb_written: boolean;
  breadcrumb_error: string | null;
};

export type ChatCompactionSummary = {
  chat_slug: string;
  filename: string;
  summary_path: string;
  content: string;
};

/**
 * Summarize the current provider session, persist the compaction document,
 * write transcript markers, and re-register the agent against the new
 * provider session.
 */
export function compactAgent(
  agentSlug: string,
  reason: CompactAgentReason = "manual",
): Promise<CompactAgentResponse> {
  return fetch(`/api/agents/${agentSlug}/compact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  }).then((r) => jsonOrThrow<CompactAgentResponse>(r));
}

export function getAgentCompactionSummary(
  agentSlug: string,
  filename: string,
): Promise<AgentCompactionSummary> {
  return fetch(
    `/api/agents/${agentSlug}/compactions/${encodeURIComponent(filename)}`,
  ).then((r) => jsonOrThrow<AgentCompactionSummary>(r));
}

export function compactChat(
  chatSlug: string,
  reason: CompactAgentReason = "manual",
): Promise<CompactChatResponse> {
  return fetch(`/api/chats/${chatSlug}/compact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  }).then((r) => jsonOrThrow<CompactChatResponse>(r));
}

export function getChatCompactionSummary(
  chatSlug: string,
  filename: string,
): Promise<ChatCompactionSummary> {
  return fetch(
    `/api/chats/${chatSlug}/compactions/${encodeURIComponent(filename)}`,
  ).then((r) => jsonOrThrow<ChatCompactionSummary>(r));
}

/**
 * Open one of the agent's filesystem locations in the OS file browser.
 * Same shell-out pattern as ``revealWork``.
 *
 * ``kind`` picks the target:
 *   - ``worktree`` (default) — the per-agent git worktree (or source
 *     folder fallback) where the SDK runs.
 *   - ``atelier`` — Atelier's per-agent bookkeeping dir under
 *     ``~/Atelier/works/<work>/agents/<agent>/`` (transcript, agent.json,
 *     contexts/).
 */
export function revealAgent(
  agentSlug: string,
  kind: "worktree" | "atelier" = "worktree",
): Promise<void> {
  const qs = kind === "worktree" ? "" : `?kind=${encodeURIComponent(kind)}`;
  return fetch(`/api/agents/${agentSlug}/reveal${qs}`, {
    method: "POST",
  }).then(async (r) => {
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      throw new Error(`${r.status} ${r.statusText}: ${body}`);
    }
  });
}

/**
 * Open the user's terminal at the agent's worktree (or source folder,
 * if no worktree was provisioned). Backend shells out to the platform
 * terminal — same pattern as ``revealAgent`` but launches a console
 * instead of the file browser.
 *
 * ``kind`` selects one of the backend-described terminal options from
 * ``GET /api/settings``; unknown values fall back to ``system``
 * server-side.
 */
export function openAgentInConsole(
  agentSlug: string,
  kind?: string,
): Promise<void> {
  const qs = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  return fetch(`/api/agents/${agentSlug}/open-in-console${qs}`, {
    method: "POST",
  }).then(async (r) => {
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      throw new Error(`${r.status} ${r.statusText}: ${body}`);
    }
  });
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
// Per-type status vocabularies returned by the backend. PR/Jira values
// are author-set; doc values are derived from observed git state at
// list time (pending = worktree-uncommitted, committed = worktree
// matches HEAD, draft = shared folder).
export type PrStatus = "draft" | "open" | "merged" | "closed";
export type DocStatus = "draft" | "pending" | "committed";
export type JiraStatus =
  | "todo"
  | "in_progress"
  | "in_review"
  | "done"
  | "closed"
  | "blocked";

export type ArtifactSummary = {
  slug: string;
  type: ArtifactType;
  title: string;
  // Per-type vocabulary — see PrStatus / DocStatus / JiraStatus above.
  // For docs the backend derives this from observed file state so the
  // FE renders one pill per artifact regardless of type.
  status: string;
  created_at: string;
  // The agent that emitted the marker, if attribution was supplied.
  agent_slug: string | null;
  url: string | null;
  repo: string | null;
  // Absolute path on disk, for doc-type artifacts; click → revealArtifact.
  doc_path: string | null;
  // Doc-only enrichment, computed on each list call. ``null`` for
  // PR/Jira and for stale doc rows whose path no longer resolves.
  location_kind: ArtifactLocation | null;
};

export function listArtifacts(workSlug: string): Promise<ArtifactSummary[]> {
  return fetch(`/api/works/${workSlug}/artifacts`).then((r) =>
    jsonOrThrow<ArtifactSummary[]>(r),
  );
}

export type RefreshPrStatusesResponse = {
  // False when the backend throttled the call or the poller isn't
  // available — UI treats this as "current cached data is fresh
  // enough", no follow-up refetch needed.
  ran: boolean;
  checked: number;
  updated: number;
  skipped: number;
  not_modified: number;
};

/**
 * Trigger an out-of-band PR status refresh on the backend. The
 * backend throttles to ~30s between actual fetches so bouncing
 * between work tabs doesn't fan out per-click GitHub requests.
 */
export function refreshPrStatuses(): Promise<RefreshPrStatusesResponse> {
  return fetch(`/api/artifacts/refresh-pr-statuses`, {
    method: "POST",
  }).then((r) => jsonOrThrow<RefreshPrStatusesResponse>(r));
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

// ---------------------------------------------------------------------------
// User settings (singleton resource)
// ---------------------------------------------------------------------------

export type UserSettingsRead = {
  editor: string;
  terminal: string;
  layout: string;
  accent_hue: number;
  theme: string;
  editor_options?: SettingsToolOption[];
  terminal_options?: SettingsToolOption[];
};

export type SettingsToolOption = {
  value: string;
  label: string;
  command: string;
  url_template?: string | null;
};

export type UserSettingsWrite = Partial<{
  editor: string;
  terminal: string;
  layout: string;
  accent_hue: number;
  theme: string;
}>;

export function getSettings(): Promise<UserSettingsRead> {
  return fetch("/api/settings").then((r) => jsonOrThrow<UserSettingsRead>(r));
}

export function putSettings(
  patch: UserSettingsWrite,
): Promise<UserSettingsRead> {
  return fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }).then((r) => jsonOrThrow<UserSettingsRead>(r));
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
