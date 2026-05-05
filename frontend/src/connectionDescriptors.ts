import { useEffect, useState } from "react";

import {
  type ConnectionDescriptor,
  type ConnectionType,
  listConnectionTypes,
} from "./api";

/**
 * Per-type form descriptors fetched from `GET /api/connections/types`.
 *
 * The backend is the source of truth — we cache the response in module
 * scope so every component that needs descriptors (Connections page,
 * NewWorkDialog, NewAgentDialog, ContextRow) hits the network once per
 * tab. The descriptors don't change at runtime; if the user adds a new
 * connection type on the server, a page reload picks it up.
 */
let cache: Promise<ConnectionDescriptor[]> | null = null;

export function getConnectionDescriptors(): Promise<ConnectionDescriptor[]> {
  if (cache === null) cache = listConnectionTypes();
  return cache;
}

export function useConnectionDescriptors(): {
  descriptors: ConnectionDescriptor[] | null;
  byType: Record<ConnectionType, ConnectionDescriptor> | null;
  loading: boolean;
  error: string | null;
} {
  const [descriptors, setDescriptors] = useState<ConnectionDescriptor[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getConnectionDescriptors()
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

  const byType =
    descriptors === null
      ? null
      : (Object.fromEntries(
          descriptors.map((d) => [d.type, d]),
        ) as Record<ConnectionType, ConnectionDescriptor>);

  return { descriptors, byType, loading: descriptors === null && error === null, error };
}
