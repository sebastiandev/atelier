import { useEffect, useState } from "react";

import {
  type ModelMeta,
  type OpenCodeModelOption,
  type ProviderDescriptor,
  type ProviderField,
  listOpenCodeModels,
  listProviders,
} from "./api";

/**
 * Per-provider form descriptors fetched from `GET /api/providers`.
 *
 * Mirrors `connectionDescriptors.ts` — module-scope cache so every
 * consumer (NewAgentDialog, AgentTile's TurnMetricsBar) shares one
 * fetch per tab. The descriptors don't change at runtime; reload picks
 * up server-side changes.
 */
let cache: Promise<ProviderDescriptor[]> | null = null;

export function getProviderDescriptors(): Promise<ProviderDescriptor[]> {
  if (cache === null) cache = listProviders();
  return cache;
}

export function useProviderDescriptors(): {
  descriptors: ProviderDescriptor[] | null;
  byName: Record<string, ProviderDescriptor> | null;
  loading: boolean;
  error: string | null;
} {
  const [descriptors, setDescriptors] = useState<ProviderDescriptor[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getProviderDescriptors()
      .then(async (data) => {
        if (cancelled) return;
        setDescriptors(data);
        // OpenCode's model list depends on which providers the user has
        // authenticated, so the descriptor ships a placeholder and the real
        // list comes from `opencode models`. Enriching here means every
        // consumer of this hook gets it, rather than each caller
        // rediscovering it.
        if (!data.some((item) => item.name === "opencode")) return;
        try {
          const rows = await listOpenCodeModels();
          if (cancelled || rows.length === 0) return;
          setDescriptors((current) =>
            (current ?? data).map((item) =>
              item.name === "opencode" ? withOpenCodeModelOptions(item, rows) : item,
            ),
          );
        } catch {
          // Leave the placeholder in place: the CLI may not be installed,
          // which is not an error for users on other providers.
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const byName =
    descriptors === null
      ? null
      : Object.fromEntries(descriptors.map((d) => [d.name, d]));

  return {
    descriptors,
    byName,
    loading: descriptors === null && error === null,
    error,
  };
}

/** Resolve the ModelMeta for a (provider, model-or-mode) pair, or null
 *  if descriptors haven't loaded, the provider is unknown, or the
 *  provider didn't publish meta for that key. Callers must tolerate
 *  null and degrade to "—". */
export function lookupModelMeta(
  byName: Record<string, ProviderDescriptor> | null,
  provider: string | undefined,
  model: string | undefined,
): ModelMeta | null {
  if (!byName || !provider || !model) return null;
  return byName[provider]?.model_meta?.[model] ?? null;
}

export type ProviderOptionSelection = {
  key: string;
  field: ProviderField;
};

/**
 * Every spelling of the reasoning-effort dial, across both the static
 * provider descriptors (`thinking_effort` for the Claude family,
 * `reasoning_effort` for Codex and OpenCode) and the live ACP session
 * config ids (`effort` for claude-acp and opencode, `reasoning_effort`
 * for codex-acp). Mirrors `EFFORT_OPTION_KEYS` + `ACP_EFFORT_CONFIG_ID`
 * in `backend/src/domain/agents/effort.py`; the pre-launch form and the
 * running-session picker read the same list so a provider can't light
 * up in one surface and stay dark in the other.
 */
export const EFFORT_OPTION_KEYS = [
  "thinking_effort",
  "reasoning_effort",
  "effort",
] as const;

const PROVIDER_PERMISSION_OPTION_KEYS = [
  "permission_mode",
  "mode",
  "approval_mode",
] as const;

export function providerDefaults(
  provider: ProviderDescriptor,
  model: string | null = provider.primary_field.default,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, field] of Object.entries(provider.options)) {
    out[key] = optionFieldForModel(provider, model, key, field).default;
  }
  for (const [key, field] of Object.entries(provider.text_options ?? {})) {
    out[key] = field.default;
  }
  return out;
}

export function coerceProviderOptionsForModel(
  provider: ProviderDescriptor,
  model: string,
  current: Record<string, string>,
): Record<string, string> {
  let changed = false;
  const next = { ...current };
  for (const [key, field] of Object.entries(provider.options)) {
    const effectiveField = optionFieldForModel(provider, model, key, field);
    const value = next[key] ?? effectiveField.default;
    if (!effectiveField.values.includes(value)) {
      next[key] = effectiveField.default;
      changed = true;
    } else if (next[key] === undefined) {
      next[key] = value;
      changed = true;
    }
  }
  return changed ? next : current;
}

export function providerOptionsPayload(
  provider: ProviderDescriptor | undefined,
  model: string | null,
  current: Record<string, string>,
): Record<string, string> | undefined {
  if (!provider) return undefined;
  const out: Record<string, string> = {};
  for (const [key, field] of Object.entries(provider.options)) {
    const effectiveField = optionFieldForModel(provider, model, key, field);
    const value = current[key];
    if (value !== undefined && value !== effectiveField.default) {
      out[key] = value;
    }
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

export function optionFieldForModel(
  provider: ProviderDescriptor,
  model: string | null,
  key: string,
  field: ProviderField,
): ProviderField {
  if (!EFFORT_OPTION_KEYS.some((item) => item === key) || !model) {
    return field;
  }
  const meta = provider.model_meta?.[model];
  const declared = meta?.effort_values;
  // Absent means "no opinion" — keep the provider-wide ladder. An empty
  // list is an opinion: this model has no effort dial (OpenCode models
  // without variants), so hand back an empty field and let callers hide
  // the control rather than offer a value the agent would ignore.
  if (declared === undefined || declared === null) return field;
  const values = declared.filter(Boolean);
  if (values.length === 0) return { ...field, values: [] };
  const defaultValue =
    meta?.effort_default && values.includes(meta.effort_default)
      ? meta.effort_default
      : values[0];
  return {
    ...field,
    values,
    default: defaultValue,
  };
}

export function providerEffortOption(
  provider: ProviderDescriptor,
  model: string | null,
): ProviderOptionSelection | null {
  for (const key of EFFORT_OPTION_KEYS) {
    const field = provider.options[key];
    if (!field) continue;
    const effective = optionFieldForModel(provider, model, key, field);
    return effective.values.length > 0 ? { key, field: effective } : null;
  }
  return null;
}

export function providerFastOption(
  provider: ProviderDescriptor,
): ProviderOptionSelection | null {
  const field = provider.options["fast-mode"];
  return field?.values.includes("on") && field.values.includes("off")
    ? { key: "fast-mode", field }
    : null;
}

export function providerPermissionOption(
  provider: ProviderDescriptor,
): ProviderOptionSelection | null {
  for (const key of PROVIDER_PERMISSION_OPTION_KEYS) {
    const field = provider.options[key];
    if (field) return { key, field };
  }
  return null;
}

export function withOpenCodeModelOptions(
  provider: ProviderDescriptor,
  models: OpenCodeModelOption[],
): ProviderDescriptor {
  const baseValue = provider.primary_field.default;
  const baseLabel =
    provider.primary_field.value_labels?.[
      provider.primary_field.values.indexOf(baseValue)
    ] ?? "OpenCode default (set in OpenCode config)";
  const values = [baseValue];
  const valueLabels = [baseLabel];
  const seen = new Set(values);
  // OpenCode calls reasoning effort a "variant" and defines the ladder
  // per model, so the CLI listing is the only source for it. Publishing
  // it as model_meta lets the existing per-model narrowing apply.
  const modelMeta: Record<string, ModelMeta> = { ...(provider.model_meta ?? {}) };
  // The sentinel routes to whatever the user's OpenCode config selects,
  // so its variant ladder is unknowable — declare no effort rather than
  // offer the union and hope.
  modelMeta[baseValue] = { ...emptyModelMeta, ...modelMeta[baseValue], effort_values: [] };
  for (const option of models) {
    if (seen.has(option.value)) continue;
    seen.add(option.value);
    values.push(option.value);
    valueLabels.push(option.label);
    if (option.effort_values === undefined) continue;
    modelMeta[option.value] = {
      ...emptyModelMeta,
      ...modelMeta[option.value],
      effort_values:
        option.effort_values.length > 0
          ? [defaultEffort, ...option.effort_values]
          : [],
      effort_default: defaultEffort,
    };
  }
  return {
    ...provider,
    primary_field: {
      ...provider.primary_field,
      values,
      value_labels: valueLabels,
    },
    model_meta: modelMeta,
  };
}

/** OpenCode's "leave it alone" effort — must match `OpenCodeEffort.DEFAULT`
 *  so the option is omitted from the launch payload when it's selected. */
const defaultEffort = "default";

const emptyModelMeta: ModelMeta = {
  context_window: null,
  input_per_mtok: null,
  output_per_mtok: null,
  cache_read_per_mtok: null,
  cache_write_per_mtok: null,
};

export function modelPickerOptions(provider: ProviderDescriptor) {
  return provider.primary_field.values.map((value, index) => ({
    value,
    label: provider.primary_field.value_labels?.[index] ?? value,
  }));
}

export function optionLabel(field: ProviderField, value: string): string {
  const idx = field.values.indexOf(value);
  return idx >= 0 ? field.value_labels?.[idx] ?? value : value;
}
