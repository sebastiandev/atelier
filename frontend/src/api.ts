export type WorkStatus = "active" | "completed" | "deleted";
export type WorkModeKind = "manual" | "planning" | "loop";

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
  mode?: WorkModeKind | null;
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
  mode?: WorkModeKind | null;
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

export type TranscriptEvent = {
  seq: number;
  type: string;
  ts: string;
  [key: string]: unknown;
};

export type TranscriptChunk = {
  events: TranscriptEvent[];
  oldest_seq: number | null;
  has_older: boolean;
};

export type TranscriptResource = "agents" | "chats";

export function getTranscriptChunk(
  resource: TranscriptResource,
  slug: string,
  beforeSeq: number,
  limit: number,
): Promise<TranscriptChunk> {
  const params = new URLSearchParams({
    before_seq: String(beforeSeq),
    limit: String(limit),
  });
  return fetch(`/api/${resource}/${slug}/transcript?${params.toString()}`).then(
    (r) => jsonOrThrow<TranscriptChunk>(r),
  );
}


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
  payload: Partial<Pick<WorkDetail, "name" | "description" | "status" | "mode" | "contexts">>,
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

export type PlanningChatReadiness = {
  ready: boolean;
  summary: string;
};

/** Mirrors backend `ChatRole` (domain/chats/posture.py). */
export type ChatRole = "explore" | "advisory" | "planning";

export type ChatSummary = {
  slug: string;
  title: string;
  provider: string;
  model: string;
  options?: Record<string, unknown> | null;
  discussion_only?: boolean;
  discussion_key?: string | null;
  role?: ChatRole;
  grounding: ChatGrounding | null;
  working_directory: string | null;
  created_at: string;
  updated_at: string;
  promoted_to_work_slug: string | null;
  message_count: number;
  planning_readiness?: PlanningChatReadiness | null;
};

export type ChatDetail = ChatSummary & {
  transcript: ChatMessage[];
};

export type CreateChatPayload = {
  provider: string;
  model: string;
  first_message?: string;
  title?: string | null;
  grounding?: ChatGrounding | null;
  working_directory?: string | null;
  options?: Record<string, string>;
  discussion_only?: boolean;
  context_seed?: string;
  discussion_key?: string;
  /** What the chat is for. The creator declares it; the backend never
   *  infers it from the title. Drives permission posture + conduct. */
  role?: ChatRole;
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

export async function reconnectChat(slug: string): Promise<void> {
  const r = await fetch(`/api/chats/${slug}/reconnect`, { method: "POST" });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`Reconnect failed (${r.status}): ${detail}`);
  }
}

export async function reconnectAgent(slug: string): Promise<void> {
  const r = await fetch(`/api/agents/${slug}/reconnect`, { method: "POST" });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`Reconnect failed (${r.status}): ${detail}`);
  }
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
export type GitBranchListing = {
  path: string;
  branches: string[];
  is_git_repo: boolean;
  branch: string | null;
  detached: boolean;
};

export function getGitBranchListing(path: string): Promise<GitBranchListing> {
  const qs = new URLSearchParams({ path });
  return fetch(`/api/git/branches?${qs}`).then(async (r) => {
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      throw new Error(`${r.status} ${r.statusText}: ${body}`);
    }
    return (await r.json()) as GitBranchListing;
  });
}

export function listGitBranches(path: string): Promise<string[]> {
  return getGitBranchListing(path).then((data) => data.branches);
}

export type CompleteWorkResponse = {
  work_slug: string;
  agent_count: number;
  workspace_count: number;
  workspaces_removed: number;
};

export type CompletionWorkspace = {
  owner: string;
  path: string;
  is_git_repo: boolean;
  branch: string | null;
  head: string | null;
  changed_files: string[];
  untracked_files: string[];
  removable: boolean;
  error: string | null;
};

export type CompleteWorkPreview = {
  work_slug: string;
  agent_count: number;
  active_run_count: number;
  workspaces: CompletionWorkspace[];
};

export function getWorkCompletion(slug: string): Promise<CompleteWorkPreview> {
  return fetch(`/api/works/${slug}/completion`).then((r) =>
    jsonOrThrow<CompleteWorkPreview>(r),
  );
}

/** Archive a Work, preserving its durable records and workspaces by default. */
export function completeWork(
  slug: string,
  payload?: { remove_workspaces: boolean },
): Promise<CompleteWorkResponse> {
  return fetch(`/api/works/${slug}/complete`, {
    method: "POST",
    headers: payload ? { "Content-Type": "application/json" } : undefined,
    body: payload ? JSON.stringify(payload) : undefined,
  }).then((r) =>
    jsonOrThrow<CompleteWorkResponse>(r),
  );
}

/**
 * Permanently delete a work. Backend stops live agents/chats, removes
 * worktrees, deletes associated work chats, and removes the Atelier work
 * folder. This cannot be undone.
 */
export async function deleteWork(slug: string): Promise<void> {
  const r = await fetch(`/api/works/${slug}`, { method: "DELETE" });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`Delete failed (${r.status}): ${detail}`);
  }
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

// ─── Work planning ───────────────────────────────────────────────────────

export type PlanningProfile =
  | "feature"
  | "refactor"
  | "migration"
  | "bugfix"
  | "hotfix"
  | "full_app"
  | "custom";

export type PlanningFramework = "bmad" | "spec" | "openspec" | "custom";
export type PlanningDepth =
  | "minimal"
  | "lightweight"
  | "standard"
  | "deep"
  | "custom";

export type PlanArtifactKind =
  | "brief"
  | "architecture"
  | "spec"
  | "scenario"
  | "acceptance"
  | "story"
  | "task"
  | "spike"
  | "bug"
  | "hotfix"
  | "note";

export type PlanArtifactStatus = "draft" | "approved" | "changed" | "accepted";
export type PlanReadiness = "ready" | "needs_detail";
export type PlanningPhase = "conversing" | "planned";
export type PlanRunStatus =
  | "running"
  | "needs_attention"
  | "waiting_approval"
  | "blocked"
  | "completed_pending_review"
  | "accepted";
export type LoopStatus =
  | "pending"
  | "running"
  | "waiting_report"
  | "assessing"
  | "needs_agent"
  | "blocked_user"
  | "completed"
  | "awaiting_approval"
  | "accepted"
  | "cleaned"
  | "failed"
  | "cancelled";

export type LoopStepStatus =
  | "pending"
  | "running"
  | "blocked_user"
  | "skipped"
  | "passed"
  | "changes_requested"
  | "failed"
  | "cancelled";

export type LoopDefinitionScope = "builtin" | "library" | "work";
export type LoopStepKind =
  | "agent_task"
  | "agent_review"
  | "deterministic_check"
  | "user_approval"
  | "pr";
export type LoopPermission = "read" | "write";
export type LoopSessionPolicy = "reuse" | "fresh";
export type LoopContextKind =
  | "target"
  | "plan_index"
  | "artifact_dependencies"
  | "workspace_diff"
  | "changed_files"
  | "previous_report"
  | "files"
  | "folder"
  | "note"
  | "shared_context"
  | "waived_findings"
  | "feedback";
export type LoopOutcome =
  | "pass"
  | "changes_requested"
  | "blocked_user"
  | "failed";
export type LoopReviewGateMode = "automatic" | "human_check";

export type LoopReviewGate = {
  mode: LoopReviewGateMode;
  max_passes: number;
  locked: boolean;
};

export type LoopReviewGateState = {
  stage_id: string;
  destination_id: string;
  mode: LoopReviewGateMode;
  source: "template" | "run_override";
  max_passes: number;
  passes_used: number;
  passes_spent: boolean;
  summary: string;
  findings: string[];
  finding_details: Array<{
    text: string;
    severity: "high" | "medium" | "low" | "resolved";
    location: string;
  }>;
};

export type LoopContextReference = {
  kind: LoopContextKind;
  required: boolean;
  paths: string[];
  step: string | null;
  ref: string | null;
};

/** One earlier stage report a stage declares that it reads. */
export type LoopReportReference = {
  /** A stage id, or "previous" for whichever stage reported last. */
  from: string;
  required: boolean;
};

/** How much of the run's own account a stage is given. */
export type LoopHistoryLevel = "none" | "summaries" | "full";

export type LoopAgentPolicy = {
  session: LoopSessionPolicy;
  permissions: LoopPermission | null;
  provider: string | null;
  model: string | null;
  effort: string | null;
  fast?: boolean | null;
  approved_command_prefixes?: string[] | null;
};

export type LoopRetryPolicy = {
  max_attempts: number;
  timeout_minutes: number;
};

export type PrConfig = {
  name: string;
  description_mode: "automatic" | "manual";
  description_instructions: string;
  manual_body: string;
  status: "draft" | "open";
  base_branch: string;
  branch_name: string;
  provider?: string | null;
  model?: string | null;
  effort?: string | null;
  fast?: boolean | null;
};

export type PrStageConfig = {
  name_template: string;
  description_mode: "automatic" | "manual";
  description_instructions: string;
  manual_body: string;
  status: "draft" | "open";
  base_branch: string;
  branch_name: string | null;
};

export type PrFeedbackPayload = {
  comments: Array<{ comment_id: string; instruction: string }>;
  instruction: string;
};

export type PrLifecycle = {
  url: string;
  number: number | null;
  title: string;
  branch: string;
  base: string;
  /** Tip of the PR branch at the last lifecycle fetch — the commit Atelier
   *  cites when it replies to an addressed comment. */
  head_sha?: string;
  head_commit_url?: string;
  status: "draft" | "open" | "merged" | "closed";
  checks: { passed?: number; total?: number; state: string } | string;
  review_state: string;
  last_synced_at: string | null;
};

export type PrComment = {
  id: string;
  author: string;
  location: string;
  body: string;
  created_at: string;
  url: string;
  kind?: "conversation" | "review";
  reply_target_id?: string;
  is_viewer?: boolean;
  addressed_in_pass?: number | null;
  reply_posted_at?: string | null;
};

export type LoopStepDefinition = {
  id: string;
  name: string;
  kind: LoopStepKind;
  instructions: string;
  inputs: LoopContextReference[];
  reports?: LoopReportReference[];
  history?: LoopHistoryLevel;
  agent: LoopAgentPolicy | null;
  retry: LoopRetryPolicy;
  transitions: Partial<Record<LoopOutcome, string | null>>;
  check_adapter: string | null;
  check_command: string[];
  note_required?: boolean | null;
  review_gate?: LoopReviewGate | null;
  pr_config?: PrStageConfig | null;
  stage_ref?: { definition_id: string; revision: string } | null;
  overrides?: StageOverrides | null;
};

export type StageOverrides = {
  name?: string | null;
  instructions?: string | null;
  inputs?: LoopContextReference[] | null;
  reports?: LoopReportReference[] | null;
  history?: LoopHistoryLevel | null;
  agent?: LoopAgentPolicy | null;
  retry?: LoopRetryPolicy | null;
  check_adapter?: string | null;
  check_command?: string[] | null;
  note_required?: boolean | null;
  review_gate?: LoopReviewGate | null;
  pr_config?: PrStageConfig | null;
};

export type StageDefinition = {
  id: string;
  name: string;
  description: string;
  scope: Exclude<LoopDefinitionScope, "work">;
  revision: string;
  valid: boolean;
  errors: string[];
  forked_from: string | null;
  used_by: string[];
  stage: LoopStepDefinition;
  outcomes: LoopOutcome[];
  /** Frontend-only origin used when global and repository catalogs are merged. */
  catalog_root?: string | null;
};

export type SaveStageDefinitionPayload = Pick<
  StageDefinition,
  "id" | "name" | "description" | "scope" | "forked_from" | "stage" | "outcomes"
> & { expected_revision?: string | null };

export type LoopBriefContextKind = "file" | "folder" | "url" | "note";

export type LoopBriefContext = {
  kind: LoopBriefContextKind;
  value: string;
};

export type LoopBriefAgent = {
  provider: string | null;
  model: string | null;
  options: Record<string, string>;
};

export type LoopStageBrief = {
  stage_id: string;
  note: string;
  context: LoopBriefContext[];
  agent: LoopBriefAgent | null;
  review_gate?: LoopReviewGateMode | null;
  approved_command_prefixes?: string[] | null;
};

export type LoopBrief = {
  goal: string;
  stages: LoopStageBrief[];
};

export type LoopRunKind = "initial" | "amend" | "verify";

export type LoopDefinition = {
  id: string;
  name: string;
  description: string;
  scope: LoopDefinitionScope;
  revision: string;
  valid: boolean;
  errors: string[];
  is_default: boolean;
  forked_from: string | null;
  stages: LoopStepDefinition[];
};

export type LoopDefinitionSnapshot = Pick<
  LoopDefinition,
  "id" | "name" | "description" | "scope" | "revision" | "stages"
>;

export type SaveLoopDefinitionPayload = Pick<
  LoopDefinition,
  "id" | "name" | "description" | "scope" | "forked_from" | "stages"
> & { expected_revision?: string | null };
export type PlanProposalStatus = "pending" | "accepted" | "rejected";
export type PlanTrackingKind = "jira" | "pr" | "blocker" | "bug";

export type PlanOverview = {
  total: number;
  ready: number;
  needs_detail: number;
  approved: number;
  changed: number;
  accepted: number;
  executable: number;
  running: number;
  review: number;
  blocked: number;
};

export type PlanLoopStageRun = {
  id: string;
  name: string;
  kind: LoopStepKind;
  status: LoopStepStatus;
  attempt: number;
  max_attempts: number;
  agent_slug: string | null;
  permissions: LoopPermission | null;
  session: LoopSessionPolicy | null;
  summary: string;
  findings: string[];
  changes: string;
  validation_evidence: string;
  divergences: string;
  skipped_scope: string;
  blocker: string;
  artifact_refs: string[];
  finding_details: Array<{
    text: string;
    severity: "high" | "medium" | "low" | "resolved";
    location: string;
  }>;
  criteria_coverage: Array<{ text: string; met: boolean; note: string }>;
  changed_files: Array<{ path: string; additions: number; deletions: number }>;
  resolved_context: string[];
  context_warnings: string[];
  reports: PlanLoopStageReport[];
  push_at?: string | null;
  pr?: PrLifecycle | null;
  addressed_comments?: Array<Record<string, unknown>>;
  feedback_instruction?: string;
  approved_command_prefixes?: string[];
};

export type PlanLoopStageReport = {
  outcome: LoopOutcome;
  pass_number: number;
  agent_slug: string | null;
  summary: string;
  findings: string[];
  changes: string;
  validation_evidence: string;
  divergences: string;
  skipped_scope: string;
  blocker: string;
  artifact_refs: string[];
  finding_details: PlanLoopStageRun["finding_details"];
  criteria_coverage: PlanLoopStageRun["criteria_coverage"];
  changed_files: PlanLoopStageRun["changed_files"];
  seq: number;
  recorded_at: string;
  review_decision?: {
    decision: "send_back" | "approve_as_is";
    enforced_findings: number[];
    instruction: string;
  } | null;
  push_at?: string | null;
  pr?: PrLifecycle | null;
  addressed_comments?: Array<Record<string, unknown>>;
  feedback_instruction?: string;
};

export type PlanArtifactRun = {
  id: string;
  agent_slug: string;
  status: PlanRunStatus;
  started_at: string;
  completed_at: string | null;
  cleanup_at: string | null;
  report_path: string | null;
  summary: string;
  divergences: string;
  skipped_scope: string;
  blockers: string;
  decisions: string;
  changes: string;
  validation_evidence: string;
  loop_status: LoopStatus | null;
  loop_status_reason: string;
  loop_attempt: number;
  loop_latest_assessment: string[];
  loop_definition_id: string;
  loop_definition_name: string;
  loop_definition_revision: string;
  loop_definition?: LoopDefinitionSnapshot | null;
  loop_current_stage_id: string;
  loop_stages: PlanLoopStageRun[];
  brief_note?: string;
  /** The whole pinned brief, so a new run can seed setup from this one. */
  brief?: LoopBrief | null;
  loop_review_gate?: LoopReviewGateState | null;
  waived_findings_count?: number;
  /** Findings the user chose not to act on, run-wide. */
  waived_findings?: string[];
  loop_pass_number?: number;
  loop_passes?: Array<Record<string, unknown>>;
  pr?: PrLifecycle | null;
  pr_comments?: PrComment[];
};

export type WorkLoopRun = {
  id: string;
  number: number;
  goal: string;
  status: LoopStatus;
  status_reason: string;
  loop_definition_id: string;
  loop_definition_name: string;
  loop_definition_revision: string;
  loop_definition?: LoopDefinitionSnapshot | null;
  current_stage_id: string | null;
  stages: PlanLoopStageRun[];
  root_path: string;
  workspace_path: string;
  provider: string;
  model: string;
  options: Record<string, string>;
  started_at: string;
  completed_at: string | null;
  accepted_at: string | null;
  cancelled_at: string | null;
  cleanup_at: string | null;
  elapsed_seconds: number | null;
  cost_usd: number | null;
  summary: string;
  changed_files: Array<{ path: string; additions: number; deletions: number }>;
  evidence: string[];
  source_run_id: string | null;
  brief?: LoopBrief | null;
  run_kind?: LoopRunKind;
  seed_label?: string;
  review_gate?: LoopReviewGateState | null;
  waived_findings_count?: number;
  /** Findings the user chose not to act on, run-wide. */
  waived_findings?: string[];
  pass_number?: number;
  passes?: Array<Record<string, unknown>>;
  pr?: PrLifecycle | null;
  pr_comments?: PrComment[];
};

export type StartWorkLoopRunPayload = {
  goal: string;
  root_path: string;
  loop_definition_id: string;
  loop_revision: string;
  provider: string;
  model: string;
  options?: Record<string, string>;
  brief?: LoopBrief;
  source_run_id?: string;
};

export type PlanArtifactProposal = {
  id: string;
  artifact_id: string;
  title: string;
  path: string;
  source_hash: string;
  proposed_content: string;
  status: PlanProposalStatus;
  created_at: string;
  resolved_at: string | null;
};

export type PlanTrackingLink = {
  id: string;
  kind: PlanTrackingKind;
  title: string;
  url: string;
  status: string;
  ref: string;
  notes: string;
  created_at: string;
};

export type PlanArtifact = {
  id: string;
  kind: PlanArtifactKind;
  title: string;
  path: string;
  source_ref: string;
  source_hash: string;
  status: PlanArtifactStatus;
  readiness: PlanReadiness;
  executable: boolean;
  launchable: boolean;
  launch_blockers: string[];
  dependencies: string[];
  runs: PlanArtifactRun[];
  proposals: PlanArtifactProposal[];
  tracking: PlanTrackingLink[];
  accepted_summary_path: string | null;
};

export type WorkPlan = {
  work_slug: string;
  framework: PlanningFramework;
  profile: PlanningProfile;
  phase: PlanningPhase;
  depth: PlanningDepth;
  root_path: string;
  planning_path: string;
  plan_artifacts_path: string;
  approved_at: string | null;
  stale: boolean;
  overview: PlanOverview;
  artifacts: PlanArtifact[];
};

export type PlanningFrameworkStatus = {
  framework: PlanningFramework;
  label: string;
  root_path: string;
  ready: boolean;
  markers: string[];
  setup_command: string[];
  setup_hint: string;
};

export type PlanMaterializationState =
  | "idle"
  | "running"
  | "waiting_permission"
  | "stalled"
  | "complete"
  | "failed";

export type PlanMaterializationStatus = {
  state: PlanMaterializationState;
  chat_slug: string | null;
  updated_at: string | null;
  last_seq: number | null;
  last_event_type: string | null;
  last_event_summary: string;
  message: string;
  tool_name: string | null;
  recent_activity?: {
    kind: string;
    text: string;
    ts: string | null;
  }[];
  pending_permissions?: {
    request_id: string;
    tool_name: string;
    tool_input: Record<string, unknown>;
    ts: string;
    seq: number;
    options?: {
      option_id: string;
      name: string;
      kind: string;
    }[];
  }[];
};

export type StartWorkPlanResult = {
  plan: WorkPlan | null;
  materialization_status: PlanMaterializationStatus;
};

export type PlanArtifactDetail = {
  artifact: PlanArtifact;
  content: string;
};

export function getWorkPlan(workSlug: string): Promise<WorkPlan> {
  return fetch(`/api/works/${workSlug}/plan`).then((r) =>
    jsonOrThrow<WorkPlan>(r),
  );
}

export function getWorkPlanMaterializationStatus(
  workSlug: string,
  init?: RequestInit,
): Promise<PlanMaterializationStatus> {
  return fetch(
    `/api/works/${workSlug}/plan/materialization-status`,
    init,
  ).then((r) => jsonOrThrow<PlanMaterializationStatus>(r));
}

export async function resolveWorkPlanMaterializationPermission(
  workSlug: string,
  requestId: string,
  decision: "allow" | "allow_always" | "deny",
): Promise<void> {
  const response = await fetch(
    `/api/works/${workSlug}/plan/materialization-permission`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id: requestId, decision }),
    },
  );
  if (!response.ok) {
    throw new Error(await response.text());
  }
}

export function startWorkPlan(
  workSlug: string,
  payload: {
    root_path?: string | null;
    plan_artifacts_dir?: string | null;
    framework?: PlanningFramework | null;
    profile?: PlanningProfile | null;
    provider?: string | null;
    model?: string | null;
    options?: Record<string, string>;
    planning_chat_slug?: string | null;
  },
): Promise<StartWorkPlanResult> {
  return fetch(`/api/works/${workSlug}/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<StartWorkPlanResult>(r));
}

export function checkPlanningFrameworkStatus(
  workSlug: string,
  payload: { root_path: string; framework: PlanningFramework },
): Promise<PlanningFrameworkStatus> {
  return fetch(`/api/works/${workSlug}/plan/framework-status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<PlanningFrameworkStatus>(r));
}

export function startPlanningChat(
  workSlug: string,
  payload: {
    root_path: string;
    idea: string;
    plan_artifacts_dir?: string | null;
    framework: PlanningFramework;
    profile: PlanningProfile;
    provider: string;
    model: string;
    options?: Record<string, string>;
  },
): Promise<ChatDetail> {
  return fetch(`/api/works/${workSlug}/planning-chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<ChatDetail>(r));
}

export function startPlanningSetupChat(
  workSlug: string,
  payload: {
    root_path: string;
    framework: PlanningFramework;
    profile: PlanningProfile;
    provider: string;
    model: string;
    options?: Record<string, string>;
  },
): Promise<ChatDetail> {
  return fetch(`/api/works/${workSlug}/planning-setup-chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<ChatDetail>(r));
}

export function finishWorkPlan(workSlug: string): Promise<WorkPlan> {
  return fetch(`/api/works/${workSlug}/plan/finish`, { method: "POST" }).then(
    (r) => jsonOrThrow<WorkPlan>(r),
  );
}

export function approveWorkPlan(workSlug: string): Promise<WorkPlan> {
  return fetch(`/api/works/${workSlug}/plan/approve`, { method: "POST" }).then(
    (r) => jsonOrThrow<WorkPlan>(r),
  );
}

export function getPlanArtifact(
  workSlug: string,
  artifactId: string,
): Promise<PlanArtifactDetail> {
  return fetch(`/api/works/${workSlug}/plan/artifacts/${artifactId}`).then((r) =>
    jsonOrThrow<PlanArtifactDetail>(r),
  );
}

export function updatePlanArtifact(
  workSlug: string,
  artifactId: string,
  payload: { content: string; expected_hash: string },
): Promise<PlanArtifactDetail> {
  return fetch(`/api/works/${workSlug}/plan/artifacts/${artifactId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<PlanArtifactDetail>(r));
}

export function acceptPlanArtifactRun(
  workSlug: string,
  artifactId: string,
  runId: string,
  payload: AcceptPlanArtifactRunPayload,
): Promise<PlanArtifactDetail> {
  return fetch(
    `/api/works/${workSlug}/plan/artifacts/${artifactId}/runs/${runId}/accept`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  ).then((r) => jsonOrThrow<PlanArtifactDetail>(r));
}

export function cancelPlanArtifactRun(
  workSlug: string,
  artifactId: string,
  runId: string,
): Promise<PlanArtifactDetail> {
  return fetch(
    `/api/works/${workSlug}/plan/artifacts/${artifactId}/runs/${runId}/cancel`,
    { method: "POST" },
  ).then((r) => jsonOrThrow<PlanArtifactDetail>(r));
}

export function stopPlanArtifactRunStage(
  workSlug: string,
  artifactId: string,
  runId: string,
): Promise<PlanArtifactDetail> {
  return fetch(
    `/api/works/${workSlug}/plan/artifacts/${artifactId}/runs/${runId}/stop-stage`,
    { method: "POST" },
  ).then((r) => jsonOrThrow<PlanArtifactDetail>(r));
}

export function requestPlanArtifactRunChanges(
  workSlug: string,
  artifactId: string,
  runId: string,
  note: string,
): Promise<PlanArtifactDetail> {
  return fetch(
    `/api/works/${workSlug}/plan/artifacts/${artifactId}/runs/${runId}/request-changes`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    },
  ).then((r) => jsonOrThrow<PlanArtifactDetail>(r));
}

export type AcceptPlanArtifactRunPayload = {
  agent_slug?: string | null;
  summary?: string;
  divergences?: string;
  skipped_scope?: string;
  blockers?: string;
  decisions?: string;
  changes?: string;
  validation_evidence?: string;
};

export function startPlanArtifactRun(
  workSlug: string,
  artifactId: string,
  definition?: Pick<LoopDefinition, "id" | "revision">,
  briefNote?: string,
  brief?: LoopBrief,
): Promise<PlanArtifactDetail> {
  return fetch(`/api/works/${workSlug}/plan/artifacts/${artifactId}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      loop_definition_id: definition?.id,
      loop_revision: definition?.revision,
      brief: brief ?? undefined,
      brief_note: briefNote || undefined,
    }),
  }).then((r) => jsonOrThrow<PlanArtifactDetail>(r));
}

export function createPlanBug(
  workSlug: string,
  artifactId: string,
  payload: { title: string; description?: string },
): Promise<PlanArtifactDetail> {
  return fetch(`/api/works/${workSlug}/plan/artifacts/${artifactId}/bugs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<PlanArtifactDetail>(r));
}

export function resumePlanArtifactRun(
  workSlug: string,
  artifactId: string,
  runId: string,
  payload: {
    resolution_note?: string;
    gate_decision?: "send_back" | "approve_as_is";
    enforced_findings?: number[];
  } = {},
): Promise<PlanArtifactDetail> {
  return fetch(
    `/api/works/${workSlug}/plan/artifacts/${artifactId}/runs/${runId}/resume`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  ).then((r) => jsonOrThrow<PlanArtifactDetail>(r));
}

export function retryPlanArtifactRunStage(
  workSlug: string,
  artifactId: string,
  runId: string,
  override?: RetryStageOverride,
): Promise<PlanArtifactDetail> {
  return fetch(
    `/api/works/${workSlug}/plan/artifacts/${artifactId}/runs/${runId}/retry-stage`,
    retryStageInit(override),
  ).then((r) => jsonOrThrow<PlanArtifactDetail>(r));
}

function loopsPath(
  workSlug: string | null,
  rootPath?: string | null,
  scope?: LoopDefinitionScope,
): string {
  const query = new URLSearchParams();
  if (workSlug) query.set("work_slug", workSlug);
  if (rootPath) query.set("root_path", rootPath);
  if (scope) query.set("scope", scope);
  const suffix = query.toString();
  return `/api/loops${suffix ? `?${suffix}` : ""}`;
}

export function listLoopDefinitions(
  workSlug: string | null,
  rootPath?: string | null,
): Promise<LoopDefinition[]> {
  return fetch(loopsPath(workSlug, rootPath)).then((response) =>
    jsonOrThrow<LoopDefinition[]>(response),
  );
}

export function saveLoopDefinition(
  workSlug: string | null,
  payload: SaveLoopDefinitionPayload,
  rootPath?: string | null,
): Promise<LoopDefinition> {
  const updating = payload.expected_revision !== null && payload.expected_revision !== undefined;
  const url = updating
    ? `/api/loops/${encodeURIComponent(payload.id)}`
    : "/api/loops";
  return fetch(url, {
    method: updating ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, work_slug: workSlug, root_path: rootPath }),
  }).then((response) => jsonOrThrow<LoopDefinition>(response));
}

export function deleteLoopDefinition(
  workSlug: string | null,
  definitionId: string,
  rootPath?: string | null,
  scope?: LoopDefinitionScope,
): Promise<void> {
  const query = loopsPath(workSlug, rootPath, scope).split("?")[1];
  return fetch(`/api/loops/${encodeURIComponent(definitionId)}${query ? `?${query}` : ""}`, {
    method: "DELETE",
  }).then((response) => {
    if (!response.ok) return jsonOrThrow<never>(response);
  });
}

export function revealLoopDefinition(
  workSlug: string | null,
  definitionId: string,
  rootPath?: string | null,
  scope?: LoopDefinitionScope,
): Promise<void> {
  const query = loopsPath(workSlug, rootPath, scope).split("?")[1];
  return fetch(
    `/api/loops/${encodeURIComponent(definitionId)}/reveal${query ? `?${query}` : ""}`,
    { method: "POST" },
  ).then((response) => {
    if (!response.ok) return jsonOrThrow<never>(response);
  });
}

function stagesPath(rootPath?: string | null): string {
  const query = new URLSearchParams();
  if (rootPath) query.set("root_path", rootPath);
  const suffix = query.toString();
  return `/api/stages${suffix ? `?${suffix}` : ""}`;
}

export function listStageDefinitions(rootPath?: string | null): Promise<StageDefinition[]> {
  return fetch(stagesPath(rootPath)).then((response) =>
    jsonOrThrow<StageDefinition[]>(response),
  );
}

export async function listAvailableStageDefinitions(
  rootPath?: string | null,
): Promise<StageDefinition[]> {
  const library = (await listStageDefinitions()).map((definition) => ({
    ...definition,
    catalog_root: null,
  }));
  if (!rootPath) return library;
  const repository = (await listStageDefinitions(rootPath)).map((definition) => ({
    ...definition,
    catalog_root: rootPath,
  }));
  return [...new Map(
    [...library, ...repository].map((definition) => [definition.id, definition]),
  ).values()];
}

export function saveStageDefinition(
  payload: SaveStageDefinitionPayload,
  rootPath?: string | null,
): Promise<StageDefinition> {
  const updating = payload.expected_revision !== null && payload.expected_revision !== undefined;
  return fetch(updating ? `/api/stages/${encodeURIComponent(payload.id)}` : "/api/stages", {
    method: updating ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, root_path: rootPath }),
  }).then((response) => jsonOrThrow<StageDefinition>(response));
}

export function deleteStageDefinition(
  stageId: string,
  rootPath?: string | null,
): Promise<void> {
  const query = stagesPath(rootPath).split("?")[1];
  return fetch(`/api/stages/${encodeURIComponent(stageId)}${query ? `?${query}` : ""}`, {
    method: "DELETE",
  }).then((response) => {
    if (!response.ok) return jsonOrThrow<never>(response);
  });
}

export function revealStageDefinition(
  stageId: string,
  rootPath?: string | null,
): Promise<void> {
  const query = stagesPath(rootPath).split("?")[1];
  return fetch(`/api/stages/${encodeURIComponent(stageId)}/reveal${query ? `?${query}` : ""}`, {
    method: "POST",
  }).then((response) => {
    if (!response.ok) return jsonOrThrow<never>(response);
  });
}

// --- Loop / stage import & export -----------------------------------------

export interface TransportExport {
  filename: string;
  content: string;
}

export type StageImportStatus = "inline" | "link-clean" | "link-conflict";

export interface StageImportPlan {
  stage_id: string;
  name: string;
  kind: string;
  status: StageImportStatus;
  linked_id: string | null;
  local_exists: boolean;
  local_scope: string | null;
  local_revision: string | null;
  used_by_count: number;
  command_prefixes: string[];
  grants_write: boolean;
}

export interface LoopImportPreview {
  name: string;
  derived_id: string;
  id_collision: boolean;
  valid: boolean;
  errors: string[];
  stages: StageImportPlan[];
}

export type StageResolutionAction = "replace" | "use_existing" | "new";

export interface StageResolutionInput {
  stage_id: string;
  action: StageResolutionAction;
  new_name?: string | null;
}

export interface StageImportPreview {
  name: string;
  derived_id: string;
  id_collision: boolean;
  same_revision: boolean;
  local_scope: string | null;
  local_revision: string | null;
  used_by_count: number;
  valid: boolean;
  errors: string[];
  command_prefixes: string[];
  grants_write: boolean;
}

export function exportLoopDefinition(
  workSlug: string | null,
  definitionId: string,
  rootPath?: string | null,
  scope?: LoopDefinitionScope,
): Promise<TransportExport> {
  const query = loopsPath(workSlug, rootPath, scope).split("?")[1];
  return fetch(
    `/api/loops/${encodeURIComponent(definitionId)}/export${query ? `?${query}` : ""}`,
  ).then((response) => jsonOrThrow<TransportExport>(response));
}

export function previewLoopImport(
  content: string,
  name?: string | null,
  rootPath?: string | null,
): Promise<LoopImportPreview> {
  return fetch("/api/loops/import/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, name: name ?? null, root_path: rootPath ?? null }),
  }).then((response) => jsonOrThrow<LoopImportPreview>(response));
}

export function importLoopDefinition(
  content: string,
  name: string | null,
  acceptedCommandPrefixes: string[],
  resolutions: StageResolutionInput[],
  rootPath?: string | null,
): Promise<LoopDefinition> {
  return fetch("/api/loops/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content,
      name: name ?? null,
      accepted_command_prefixes: acceptedCommandPrefixes,
      resolutions,
      root_path: rootPath ?? null,
    }),
  }).then((response) => jsonOrThrow<LoopDefinition>(response));
}

export function exportStageDefinition(
  stageId: string,
  rootPath?: string | null,
): Promise<TransportExport> {
  const query = stagesPath(rootPath).split("?")[1];
  return fetch(
    `/api/stages/${encodeURIComponent(stageId)}/export${query ? `?${query}` : ""}`,
  ).then((response) => jsonOrThrow<TransportExport>(response));
}

export function previewStageImport(
  content: string,
  name?: string | null,
  rootPath?: string | null,
): Promise<StageImportPreview> {
  return fetch("/api/stages/import/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, name: name ?? null, root_path: rootPath ?? null }),
  }).then((response) => jsonOrThrow<StageImportPreview>(response));
}

export function importStageDefinition(
  content: string,
  name: string | null,
  acceptedCommandPrefixes: string[],
  action?: StageResolutionAction | null,
  newName?: string | null,
  rootPath?: string | null,
): Promise<StageDefinition> {
  return fetch("/api/stages/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content,
      name: name ?? null,
      accepted_command_prefixes: acceptedCommandPrefixes,
      action: action ?? null,
      new_name: newName ?? null,
      root_path: rootPath ?? null,
    }),
  }).then((response) => jsonOrThrow<StageDefinition>(response));
}

export function listWorkLoopRuns(workSlug: string): Promise<WorkLoopRun[]> {
  return fetch(`/api/works/${workSlug}/runs`).then((response) =>
    jsonOrThrow<WorkLoopRun[]>(response),
  );
}

export function getWorkLoopRun(
  workSlug: string,
  runId: string,
): Promise<WorkLoopRun> {
  return fetch(`/api/works/${workSlug}/runs/${runId}`).then((response) =>
    jsonOrThrow<WorkLoopRun>(response),
  );
}

export function startWorkLoopRun(
  workSlug: string,
  payload: StartWorkLoopRunPayload,
): Promise<WorkLoopRun> {
  return fetch(`/api/works/${workSlug}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((response) => jsonOrThrow<WorkLoopRun>(response));
}

export function createWorkLoopRunPrStage(
  workSlug: string,
  runId: string,
  payload: PrConfig,
): Promise<WorkLoopRun> {
  return fetch(`/api/works/${workSlug}/runs/${runId}/create-pr`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((response) => jsonOrThrow<WorkLoopRun>(response));
}

export function sendWorkLoopRunPrFeedback(
  workSlug: string,
  runId: string,
  payload: PrFeedbackPayload,
): Promise<WorkLoopRun> {
  return fetch(`/api/works/${workSlug}/runs/${runId}/pr-feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((response) => jsonOrThrow<WorkLoopRun>(response));
}

export function refreshWorkLoopRunPr(
  workSlug: string,
  runId: string,
  force = false,
): Promise<WorkLoopRun> {
  const query = force ? "?force=true" : "";
  return fetch(`/api/works/${workSlug}/runs/${runId}/pr-refresh${query}`, {
    method: "POST",
  }).then((response) => jsonOrThrow<WorkLoopRun>(response));
}

export function createPlanArtifactRunPrStage(
  workSlug: string,
  artifactId: string,
  runId: string,
  payload: PrConfig,
): Promise<PlanArtifactDetail> {
  return fetch(`/api/works/${workSlug}/plan/artifacts/${artifactId}/runs/${runId}/create-pr`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((response) => jsonOrThrow<PlanArtifactDetail>(response));
}

export function sendPlanArtifactRunPrFeedback(
  workSlug: string,
  artifactId: string,
  runId: string,
  payload: PrFeedbackPayload,
): Promise<PlanArtifactDetail> {
  return fetch(`/api/works/${workSlug}/plan/artifacts/${artifactId}/runs/${runId}/pr-feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((response) => jsonOrThrow<PlanArtifactDetail>(response));
}

export function refreshPlanArtifactRunPr(
  workSlug: string,
  artifactId: string,
  runId: string,
  force = false,
): Promise<PlanArtifactDetail> {
  const query = force ? "?force=true" : "";
  return fetch(
    `/api/works/${workSlug}/plan/artifacts/${artifactId}/runs/${runId}/pr-refresh${query}`,
    { method: "POST" },
  ).then((response) => jsonOrThrow<PlanArtifactDetail>(response));
}

export function getWorkLoopBrief(workSlug: string): Promise<LoopBrief | null> {
  return fetch(`/api/works/${workSlug}/loop-brief`).then((response) =>
    jsonOrThrow<LoopBrief | null>(response),
  );
}

export function saveWorkLoopBrief(
  workSlug: string,
  brief: LoopBrief,
): Promise<LoopBrief> {
  return fetch(`/api/works/${workSlug}/loop-brief`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(brief),
  }).then((response) => jsonOrThrow<LoopBrief>(response));
}

export function resumeWorkLoopRun(
  workSlug: string,
  runId: string,
  payload: {
    resolution_note?: string;
    gate_decision?: "send_back" | "approve_as_is";
    enforced_findings?: number[];
  } = {},
): Promise<WorkLoopRun> {
  return fetch(`/api/works/${workSlug}/runs/${runId}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((response) => jsonOrThrow<WorkLoopRun>(response));
}

export interface RetryStageOverride {
  model?: string | null;
  effort?: string | null;
  /** What to do differently on this attempt. Reaches the retry agent's prompt
   *  and is not kept as run-wide requested changes. */
  note?: string;
}

/** Shared by the standalone-Loop and Planning retry verbs: the body is sent
 *  only when something is actually overridden, so a plain retry stays a
 *  bodyless POST. */
function retryStageInit(override?: RetryStageOverride): RequestInit {
  const overriding =
    override != null &&
    ((override.model != null && override.model !== "") ||
      (override.effort != null && override.effort !== "") ||
      (override.note != null && override.note.trim() !== ""));
  if (!overriding) return { method: "POST" };
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: override?.model ?? null,
      effort: override?.effort ?? null,
      note: override?.note?.trim() ?? "",
    }),
  };
}

export function retryWorkLoopRunStage(
  workSlug: string,
  runId: string,
  override?: RetryStageOverride,
): Promise<WorkLoopRun> {
  return fetch(
    `/api/works/${workSlug}/runs/${runId}/retry-stage`,
    retryStageInit(override),
  ).then((response) => jsonOrThrow<WorkLoopRun>(response));
}

export function requestWorkLoopRunChanges(
  workSlug: string,
  runId: string,
  note: string,
): Promise<WorkLoopRun> {
  return fetch(`/api/works/${workSlug}/runs/${runId}/request-changes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note }),
  }).then((response) => jsonOrThrow<WorkLoopRun>(response));
}

export function acceptWorkLoopRun(
  workSlug: string,
  runId: string,
): Promise<WorkLoopRun> {
  return fetch(`/api/works/${workSlug}/runs/${runId}/accept`, {
    method: "POST",
  }).then((response) => jsonOrThrow<WorkLoopRun>(response));
}

export function cancelWorkLoopRun(
  workSlug: string,
  runId: string,
): Promise<WorkLoopRun> {
  return fetch(`/api/works/${workSlug}/runs/${runId}/cancel`, {
    method: "POST",
  }).then((response) => jsonOrThrow<WorkLoopRun>(response));
}

export function stopWorkLoopRunStage(
  workSlug: string,
  runId: string,
): Promise<WorkLoopRun> {
  return fetch(`/api/works/${workSlug}/runs/${runId}/stop-stage`, {
    method: "POST",
  }).then((response) => jsonOrThrow<WorkLoopRun>(response));
}

export function rerunWorkLoopRun(
  workSlug: string,
  runId: string,
  payload?: { kind: Exclude<LoopRunKind, "initial">; note?: string },
): Promise<WorkLoopRun> {
  return fetch(`/api/works/${workSlug}/runs/${runId}/rerun`, {
    method: "POST",
    headers: payload ? { "Content-Type": "application/json" } : undefined,
    body: payload ? JSON.stringify(payload) : undefined,
  }).then((response) => jsonOrThrow<WorkLoopRun>(response));
}

export function rerunPlanArtifactRun(
  workSlug: string,
  artifactId: string,
  runId: string,
  payload: { kind: Exclude<LoopRunKind, "initial">; note?: string },
): Promise<PlanArtifactDetail> {
  return fetch(
    `/api/works/${workSlug}/plan/artifacts/${artifactId}/runs/${runId}/rerun`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  ).then((response) => jsonOrThrow<PlanArtifactDetail>(response));
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
  options?: Record<string, unknown> | null;
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

export function getAgent(agentSlug: string): Promise<AgentSummary> {
  return fetch(`/api/agents/${agentSlug}`).then((response) =>
    jsonOrThrow<AgentSummary>(response),
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
  workspace_mode?: "isolated" | "shared";
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

export type OpenCodeModelOption = {
  value: string;
  label: string;
  /** OpenCode's per-model "variants" — its name for reasoning effort.
   *  Absent on older backends; empty means the model has no effort dial. */
  effort_values?: string[];
};

export function listOpenCodeModels(
  options: { refresh?: boolean } = {},
): Promise<OpenCodeModelOption[]> {
  const params = new URLSearchParams();
  if (options.refresh) params.set("refresh", "true");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return fetch(`/api/providers/opencode/models${suffix}`).then((r) =>
    jsonOrThrow<OpenCodeModelOption[]>(r),
  );
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

export type ImageUploadResponse = {
  path: string;
  filename: string;
  content_type: string;
  size: number;
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

export function uploadImageAttachment(
  file: File,
  options: { workSlug?: string | null } = {},
): Promise<ImageUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const params = new URLSearchParams();
  if (options.workSlug) params.set("work_slug", options.workSlug);
  const qs = params.toString();
  return fetch(`/api/fs/uploads/images${qs ? `?${qs}` : ""}`, {
    method: "POST",
    body: form,
  }).then((r) => jsonOrThrow<ImageUploadResponse>(r));
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
  default_folder?: string | null;
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
  default_folder?: string | null;
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
  default_folder?: string | null;
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
