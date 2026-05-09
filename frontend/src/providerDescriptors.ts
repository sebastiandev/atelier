import { useEffect, useState } from "react";

import {
  type ModelMeta,
  type ProviderDescriptor,
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
      .then((data) => {
        if (!cancelled) setDescriptors(data);
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
