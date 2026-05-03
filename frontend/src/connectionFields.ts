import type { ConnectionType } from "./api";

/**
 * Per-source form schema. Mirrors `CONNECTION_FIELDS` in
 * `design_handoff_atelier/design_files/data.jsx`. The token field carries
 * `secret: true` so the input renders as a password with a reveal toggle.
 *
 * `name` lives outside `fields` because it has its own slot at the top of
 * the form (the connection title); the rest map onto the entity columns.
 */
export type ConnectionField = {
  id: string;
  label: string;
  placeholder?: string;
  required?: boolean;
  secret?: boolean;
  options?: string[];
};

export type ConnectionSchema = {
  label: string;
  glyph: string;
  docs: string;
  fields: ConnectionField[];
};

export const CONNECTION_FIELDS: Record<ConnectionType, ConnectionSchema> = {
  jira: {
    label: "Jira",
    glyph: "JR",
    docs: "Create a token at id.atlassian.com/manage-profile/security/api-tokens",
    fields: [
      { id: "name", label: "Connection name", placeholder: "Acme Eng", required: true },
      {
        id: "url",
        label: "Site URL",
        placeholder: "https://acme.atlassian.net",
        required: true,
      },
      {
        id: "email",
        label: "Account email",
        placeholder: "you@acme.com",
        required: true,
      },
      {
        id: "token",
        label: "API token",
        placeholder: "ATATT3xFf…",
        required: true,
        secret: true,
      },
    ],
  },
  sentry: {
    label: "Sentry",
    glyph: "SE",
    docs: "Generate an auth token at sentry.io › Settings › Auth Tokens",
    fields: [
      { id: "name", label: "Connection name", placeholder: "acme-prod", required: true },
      { id: "org", label: "Org slug", placeholder: "acme", required: true },
      {
        id: "region",
        label: "Region",
        placeholder: "us",
        options: ["us", "eu", "self-hosted"],
      },
      {
        id: "token",
        label: "Auth token",
        placeholder: "sntrys_…",
        required: true,
        secret: true,
      },
    ],
  },
  honeycomb: {
    label: "Honeycomb",
    glyph: "HC",
    docs: "Find an environment API key at ui.honeycomb.io › Environment Settings",
    fields: [
      {
        id: "name",
        label: "Connection name",
        placeholder: "acme · prod",
        required: true,
      },
      { id: "env", label: "Environment", placeholder: "prod", required: true },
      { id: "team", label: "Team", placeholder: "acme" },
      {
        id: "token",
        label: "API key",
        placeholder: "hcaik_…",
        required: true,
        secret: true,
      },
    ],
  },
};

export const CONNECTION_TYPES: ConnectionType[] = ["jira", "sentry", "honeycomb"];
