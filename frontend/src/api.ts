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
    body: JSON.stringify({ ...payload, contexts: [] }),
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
};

export type ProviderDescriptor = {
  name: string;
  label: string;
  primary_field: ProviderField;
  options: Record<string, ProviderField>;
};

export type CreateAgentPayload = {
  name: string;
  persona: Persona;
  role: string;
  provider: string;
  model: string;
  options?: Record<string, string>;
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
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<AgentSummary>(r));
}
