export type WorkStatus = "active" | "completed" | "archived";

export type WorkSummary = {
  slug: string;
  name: string;
  description: string;
  folder: string;
  status: WorkStatus;
  created_at: string;
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
  folder: string;
  contexts?: ContextEntry[];
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

export type Persona = "architect" | "developer" | "product" | "ux" | "writer";
export type AgentStatus = "idle" | "live" | "thinking" | "error" | "stopped";

export type AgentSummary = {
  slug: string;
  work_slug: string;
  name: string;
  persona: Persona;
  role: string;
  provider: string;
  model: string;
  status: AgentStatus;
  started_at: string;
  stopped_at: string | null;
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
