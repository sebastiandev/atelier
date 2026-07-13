/** Append ordered live events, merging only when older history overlaps. */
export function mergeEvents<T extends { seq: number }>(
  existing: T[],
  incoming: T[],
): T[] {
  if (incoming.length === 0) return existing;
  let lastSeq = existing[existing.length - 1]?.seq ?? Number.NEGATIVE_INFINITY;
  let appendOnly = true;
  for (const event of incoming) {
    if (event.seq <= lastSeq) {
      appendOnly = false;
      break;
    }
    lastSeq = event.seq;
  }
  if (appendOnly) return [...existing, ...incoming];

  const bySeq = new Map<number, T>();
  for (const event of existing) bySeq.set(event.seq, event);
  for (const event of incoming) bySeq.set(event.seq, event);
  return Array.from(bySeq.values()).sort((a, b) => a.seq - b.seq);
}
