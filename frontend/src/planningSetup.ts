import type { PlanningFramework, PlanningProfile } from "./api";

export type PlanningFrameworkId = PlanningFramework;

export type PlanningAgentConfig = {
  provider: string;
  model: string;
  options: Record<string, string>;
};

export type PlanningProfileDepth =
  | "minimal"
  | "lightweight"
  | "standard"
  | "deep"
  | "custom";

export type PlanningFrameworkDefinition = {
  id: PlanningFrameworkId;
  name: string;
  desc: string;
  chatFocus: string;
  sourceShape: string;
};

export type PlanningProfileDefinition = {
  id: PlanningProfile;
  name: string;
  icon: "spark" | "bug" | "bolt" | "branch" | "move" | "artifact" | "settings";
  depth: PlanningProfileDepth;
  artifacts: string;
  desc: string;
  chatFocus: string;
  readySignal: string;
};

export const PLANNING_FRAMEWORKS: PlanningFrameworkDefinition[] = [
  {
    id: "bmad",
    name: "BMAD",
    desc: "Brief → design → architecture → epics",
    chatFocus:
      "Use BMAD's brief -> design guide -> architecture decisions -> executable plan flow.",
    sourceShape:
      "Draft intent/problem framing, design guide, architecture notes when needed, then epics/stories/spikes.",
  },
  {
    id: "spec",
    name: "Spec-kit",
    desc: "Spec-first planning",
    chatFocus:
      "Use a spec-first flow: define the living spec, scenarios, acceptance criteria, constraints, and implementation tasks.",
    sourceShape:
      "Draft a living spec with scenarios and acceptance criteria, then derive tasks/stories from that spec.",
  },
  {
    id: "openspec",
    name: "OpenSpec",
    desc: "Proposal, spec changes, and tasks",
    chatFocus:
      "Use OpenSpec's proposal -> spec change -> tasks -> validation workflow.",
    sourceShape:
      "Draft a proposal, source-of-truth spec change, and implementation tasks.",
  },
  {
    id: "custom",
    name: "Custom",
    desc: "Bring your own templates",
    chatFocus:
      "Elicit the user's preferred artifacts, gates, and depth before committing to a plan shape.",
    sourceShape:
      "If the user has not supplied templates, propose a small source-backed artifact set and ask them to confirm.",
  },
];

export const PLANNING_PROFILES: PlanningProfileDefinition[] = [
  {
    id: "feature",
    name: "Feature",
    icon: "spark",
    depth: "lightweight",
    artifacts: "intent · design guide · stories",
    desc: "Add a capability to an existing product.",
    chatFocus:
      "Clarify the user outcome, product behavior, constraints, affected source areas, and launchable story slices.",
    readySignal:
      "Ready means the capability can be split into scoped stories with acceptance criteria and validation.",
  },
  {
    id: "bugfix",
    name: "Bug",
    icon: "bug",
    depth: "minimal",
    artifacts: "repro · fix story",
    desc: "Fix a defect with a focused, single-story plan.",
    chatFocus:
      "Extract observed behavior, expected behavior, reproduction path, suspected source area, regression test, and fix scope.",
    readySignal:
      "Ready means there is a reproducible failure or a clear investigation spike plus a focused fix story.",
  },
  {
    id: "hotfix",
    name: "Hotfix",
    icon: "bolt",
    depth: "minimal",
    artifacts: "fix story · skips planning gates",
    desc: "Urgent production fix, straight to a scoped agent.",
    chatFocus:
      "Prioritize blast radius, rollback/backout, minimal patch scope, validation, and what can safely wait.",
    readySignal:
      "Ready means the safest smallest change and verification path are clear enough to launch immediately.",
  },
  {
    id: "refactor",
    name: "Refactor",
    icon: "branch",
    depth: "standard",
    artifacts: "design guide · architecture · stories",
    desc: "Restructure code without changing behavior.",
    chatFocus:
      "Pin down invariants, non-goals, impacted modules, sequencing, compatibility constraints, and tests proving unchanged behavior.",
    readySignal:
      "Ready means each slice preserves behavior, has rollback boundaries, and can be validated independently.",
  },
  {
    id: "migration",
    name: "Migration",
    icon: "move",
    depth: "standard",
    artifacts: "architecture · migration plan · stories",
    desc: "Move or upgrade systems, data, or APIs.",
    chatFocus:
      "Map source and target states, data/backward compatibility, rollout phases, backout, validation, and operational risks.",
    readySignal:
      "Ready means the migration can be staged with compatibility and rollback clearly represented in the plan.",
  },
  {
    id: "full_app",
    name: "Full app",
    icon: "artifact",
    depth: "deep",
    artifacts: "brief · PRD · architecture · epics",
    desc: "Greenfield product with discovery and architecture.",
    chatFocus:
      "Start with product brief, audience, workflows, core data model, system boundaries, architecture, milestones, and epic breakdown.",
    readySignal:
      "Ready means the first implementation milestone has a coherent product and architecture baseline.",
  },
  {
    id: "custom",
    name: "Custom",
    icon: "settings",
    depth: "custom",
    artifacts: "choose artifacts & depth",
    desc: "Pick your own artifacts and planning depth.",
    chatFocus:
      "Ask which artifacts, review gates, depth, and launch criteria the user wants, then adapt the plan shape to that answer.",
    readySignal:
      "Ready means the custom artifact set and gating criteria are explicit enough to materialize.",
  },
];

export function planningFrameworkDefinition(
  framework: PlanningFrameworkId,
): PlanningFrameworkDefinition {
  return (
    PLANNING_FRAMEWORKS.find((item) => item.id === framework) ??
    PLANNING_FRAMEWORKS[0]
  );
}

export function planningProfileDefinition(
  profile: PlanningProfile,
): PlanningProfileDefinition {
  return (
    PLANNING_PROFILES.find((item) => item.id === profile) ??
    PLANNING_PROFILES[0]
  );
}

export function planningFrameworkLabel(framework: PlanningFrameworkId): string {
  return planningFrameworkDefinition(framework).name;
}

export function planningProfileLabel(profile: PlanningProfile): string {
  return planningProfileDefinition(profile).name;
}
