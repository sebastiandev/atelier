export type LoopStartSeed = {
  folder: string;
  goal: string;
  definitionId?: string;
  createDefinition?: boolean;
};

/** Session handoff between New Work creation and the Loop setup screen. */
export function loopStartStorageKey(workSlug: string): string {
  return `atelier:loop-start:${workSlug}`;
}
