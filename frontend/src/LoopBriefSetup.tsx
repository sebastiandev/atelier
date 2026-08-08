import { useState } from "react";

import {
  type LoopBrief,
  type LoopBriefAgent,
  type LoopBriefContext,
  type LoopBriefContextKind,
  type LoopDefinition,
  type LoopStageBrief,
  type LoopStepDefinition,
} from "./api";
import { CommandPrefixEditor } from "./CommandPrefixEditor";
import { FolderPickerDialog } from "./FolderPickerDialog";
import {
  BranchIcon,
  ChatIcon,
  CheckIcon,
  DocIcon,
  EditIcon,
  EyeIcon,
  FlaskIcon,
  FolderIcon,
  PaperclipIcon,
  PlayIcon,
  ReturnIcon,
  UserCheckIcon,
} from "./Icons";
import { LoopPicker } from "./LoopUI";
import { PlanningAgentControls } from "./PlanningMode";
import {
  providerDefaults,
  useProviderDescriptors,
} from "./providerDescriptors";
import type { ProviderDescriptor } from "./api";
import { overrideDelta, type PlanningAgentConfig } from "./planningSetup";

type Props = {
  brief: LoopBrief;
  busy: boolean;
  definition: LoopDefinition | null;
  definitions: LoopDefinition[] | null;
  error: string | null;
  folder: string;
  workSlug: string;
  onBrief: (brief: LoopBrief) => void;
  /** Choosing a loop reconfigures every section below. */
  onSelectDefinition: (definition: LoopDefinition) => void;
  onChooseFolder?: () => void;
  onEditLoop: () => void;
  /** The goal lives on the brief; leave false when the caller owns it. */
  goalEditable?: boolean;
  /** Opens a discussion beside this screen. What the agent settles on is
   *  written straight into the goal, so the two never disagree. */
  onDiscussGoal?: () => void | Promise<void>;
  /** Caption for the goal field. */
  goalLabel?: string;
  onStart: () => void;
};

/** Collect the per-work inputs and execution overrides for one Loop run. */
export function LoopBriefSetup({
  brief,
  busy,
  definition,
  definitions,
  error,
  folder,
  workSlug,
  onBrief,
  onSelectDefinition,
  onChooseFolder,
  onEditLoop,
  goalEditable = false,
  onDiscussGoal,
  goalLabel = "Goal",
  onStart,
}: Props) {
  const goal = brief.goal;
  const [expandedStageId, setExpandedStageId] = useState<string | null>(null);
  // Options already on the brief were set by a person on an earlier visit,
  // so they stay chosen across a remount.
  const [touchedOptionKeys, setTouchedOptionKeys] = useState<Set<string>>(
    () => new Set(Object.keys(entryAgentOptions(brief, definition))),
  );
  const { descriptors } = useProviderDescriptors();
  const [picker, setPicker] = useState<{
    kind: "file" | "folder";
    stageId: string;
  } | null>(null);
  const agentStages = definition?.stages.filter((stage) => stage.agent !== null) ?? [];
  const firstAgentStage = agentStages[0] ?? null;
  const firstStageBrief = firstAgentStage ? stageBrief(brief, firstAgentStage.id) : null;
  // The base execution config lives on the brief's entry stage and nowhere
  // else. It used to also live in a caller-held state that the controls
  // rendered from while Start sent the brief, so the screen could show one
  // config and launch another.
  // Complete for display, sparse in the brief. The controls render from the
  // effective config — provider defaults filled in — while the brief keeps
  // only the options a person actually set, so an untouched default never
  // outranks the loop definition.
  const agentConfig = completeAgent(
    firstStageBrief?.agent ?? null,
    defaultsFor(descriptors, firstStageBrief?.agent ?? null),
  );
  const baseCommandPrefixes = firstStageBrief?.approved_command_prefixes
    ?? firstAgentStage?.agent?.approved_command_prefixes
    ?? [];
  const slots = agentStages;
  const missing = slots.filter(
    (stage) => stage.note_required === true && !stageBrief(brief, stage.id).note.trim(),
  );
  const ready = Boolean(
    definition && agentConfig && goal.trim() && folder.trim() && missing.length === 0,
  );

  function patchStage(
    stageId: string,
    patch: Partial<ReturnType<typeof emptyStageBrief>>,
  ) {
    const current = stageBrief(brief, stageId);
    const next = { ...current, ...patch };
    onBrief({
      ...brief,
      stages: [...brief.stages.filter((stage) => stage.stage_id !== stageId), next],
    });
  }

  function setBaseConfig(config: PlanningAgentConfig, touchedKey?: string) {
    if (!firstAgentStage) return;
    // Switching provider or model hands back that provider's defaults, so
    // nothing is chosen yet; only an option a person set survives into the
    // brief. Provider and model always do: a story run's entry stage has
    // nothing to fall back on and must name one.
    const switched =
      agentConfig?.provider !== config.provider || agentConfig?.model !== config.model;
    const kept = switched
      ? new Set<string>()
      : new Set(touchedOptionKeys);
    if (touchedKey) kept.add(touchedKey);
    setTouchedOptionKeys(kept);
    const options: Record<string, string> = {};
    for (const key of kept) {
      const value = config.options[key];
      if (value !== undefined) options[key] = value;
    }
    patchStage(firstAgentStage.id, {
      agent: { provider: config.provider, model: config.model, options },
    });
  }

  function addContext(stageId: string, kind: LoopBriefContextKind, value = "") {
    const current = stageBrief(brief, stageId);
    patchStage(stageId, { context: [...current.context, { kind, value }] });
  }

  function replaceContext(stageId: string, index: number, next: LoopBriefContext) {
    const current = stageBrief(brief, stageId);
    patchStage(stageId, {
      context: current.context.map((item, itemIndex) => itemIndex === index ? next : item),
    });
  }

  function removeContext(stageId: string, index: number) {
    const current = stageBrief(brief, stageId);
    patchStage(stageId, {
      context: current.context.filter((_, itemIndex) => itemIndex !== index),
    });
  }

  return (
    <div className="loop-brief-setup">
      <header className="loop-mode-intro">
        <strong>Prepare this run</strong>
        <span>The loop is the reusable part. Brief each stage for this work — briefs live on {workSlug}, never on the loop.</span>
      </header>

      <section className="loop-brief-goal">
        <header className="loop-brief-goal-head">
          <label>{goalLabel}</label>
          {onDiscussGoal && (
            <button type="button" className="btn ghost sm" onClick={() => void onDiscussGoal()}>
              <ChatIcon size={11} /> Discuss the goal
            </button>
          )}
        </header>
        {goalEditable ? (
          <textarea
            rows={2}
            value={goal}
            onChange={(event) => onBrief({ ...brief, goal: event.target.value })}
          />
        ) : (
          <p className="loop-brief-goal-fixed">{goal}</p>
        )}
      </section>

      <section className="loop-brief-definition">
        <header>
          <label>Loop</label>
          {definition && (
            <span>{definition.name} · {scopeLabel(definition)} · rev {definition.revision}</span>
          )}
          <span className="loop-brief-definition-actions">
            <button type="button" disabled={!definition} onClick={onEditLoop}><EditIcon size={10} /> edit {definition?.scope === "builtin" ? "(forks)" : ""}</button>
          </span>
        </header>
        <LoopPicker
          definitions={definitions}
          selectedId={definition?.id ?? null}
          onSelect={onSelectDefinition}
          onCreate={onEditLoop}
        />
        {definition && <StageStrip stages={definition.stages} />}
      </section>

      {definition && (
        <section className="loop-brief-stages">
          <header>
            <label>Stage briefs</label>
            <span>{slots.length} {slots.length === 1 ? "slot" : "slots"}{missing.length > 0 ? ` · ${missing.length} required still empty` : " · ready"}</span>
          </header>
          <div className="loop-brief-list">
            {definition.stages.map((stage) => stage.agent ? (
              <StageBriefCard
                key={stage.id}
                agentConfig={agentConfig}
                baseStage={stage.id === firstAgentStage?.id}
                inheritedCommandPrefixes={baseCommandPrefixes}
                brief={stageBrief(brief, stage.id)}
                expanded={expandedStageId === stage.id}
                loopRevision={definition.revision}
                stage={stage}
                workSlug={workSlug}
                onAddContext={(kind) => {
                  if (kind === "file" || kind === "folder") setPicker({ kind, stageId: stage.id });
                  else addContext(stage.id, kind);
                }}
                onAgent={(agent) => patchStage(stage.id, { agent })}
                onBaseAgent={setBaseConfig}
                onEditLoop={onEditLoop}
                onExpand={() => setExpandedStageId((current) => current === stage.id ? null : stage.id)}
                onNote={(note) => patchStage(stage.id, { note })}
                onReviewGate={(review_gate) => patchStage(stage.id, { review_gate })}
                onApprovedCommandPrefixes={(approved_command_prefixes) => patchStage(stage.id, { approved_command_prefixes })}
                onRemoveContext={(index) => removeContext(stage.id, index)}
                onReplaceContext={(index, context) => replaceContext(stage.id, index, context)}
              />
            ) : (
              <StageBriefSlim key={stage.id} stage={stage} />
            ))}
          </div>
        </section>
      )}

      {error && <div className="form-error">{error}</div>}
      {!definition && definitions && (
        <p className="loop-brief-pending">Choose a loop above to brief its stages.</p>
      )}
      {definition && <footer className="loop-brief-footer">
        {onChooseFolder ? (
          <button type="button" className="loop-brief-workdir" onClick={onChooseFolder} title="Choose work folder">
            <FolderIcon size={11} /> {folder ? compactPath(folder) : "choose workdir"} · isolated worktree
          </button>
        ) : (
          <span className="loop-brief-workdir fixed">
            <FolderIcon size={11} /> {compactPath(folder)} · isolated worktree
          </span>
        )}
        <span />
        {goalEditable && !goal.trim() ? (
          <em>Goal is required</em>
        ) : missing.length > 0 ? (
          <em>{missing[0].name} needs a brief before this run can start</em>
        ) : slots.some((stage) => !stageBrief(brief, stage.id).note.trim()) ? (
          <small>Optional stages run on the template alone</small>
        ) : null}
        <button className="btn primary" disabled={busy || !ready} onClick={onStart}>
          <PlayIcon size={12} /> {busy ? "Starting..." : "Start loop"}
        </button>
      </footer>}

      {picker && (
        <FolderPickerDialog
          initialPath={folder || null}
          mode={picker.kind}
          onCancel={() => setPicker(null)}
          onPick={(path) => {
            addContext(picker.stageId, picker.kind, relativePath(folder, path));
            setExpandedStageId(picker.stageId);
            setPicker(null);
          }}
        />
      )}
    </div>
  );
}

function StageBriefCard({
  agentConfig,
  baseStage,
  brief,
  expanded,
  inheritedCommandPrefixes,
  loopRevision,
  stage,
  workSlug,
  onAddContext,
  onAgent,
  onBaseAgent,
  onEditLoop,
  onExpand,
  onNote,
  onReviewGate,
  onApprovedCommandPrefixes,
  onRemoveContext,
  onReplaceContext,
}: {
  agentConfig: PlanningAgentConfig | null;
  baseStage: boolean;
  brief: LoopStageBrief;
  expanded: boolean;
  inheritedCommandPrefixes: string[];
  loopRevision: string;
  stage: LoopStepDefinition;
  workSlug: string;
  onAddContext: (kind: LoopBriefContextKind) => void;
  onAgent: (agent: LoopBriefAgent | null) => void;
  onBaseAgent: (agent: PlanningAgentConfig) => void;
  onEditLoop: () => void;
  onExpand: () => void;
  onNote: (note: string) => void;
  onReviewGate: (mode: "automatic" | "human_check" | null) => void;
  onApprovedCommandPrefixes: (prefixes: string[] | null) => void;
  onRemoveContext: (index: number) => void;
  onReplaceContext: (index: number, context: LoopBriefContext) => void;
}) {
  const needsInput = stage.note_required === true && !brief.note.trim();
  const overrideConfig = completeAgent(brief.agent, agentConfig);
  const execution = overrideConfig ?? agentConfig;
  const pinned = pinnedExecution(stage);
  // What this stage's policy already fixes. The controls show these as the
  // effective values so an inherited-but-pinned effort reads as itself
  // rather than as the provider default.
  const pinnedPolicy = {
    effort: stage.agent?.effort ?? null,
    fast: stage.agent?.fast ?? null,
    permissions: stage.agent?.permissions ?? null,
  };
  const templateCommandPrefixes = baseStage
    ? stage.agent?.approved_command_prefixes ?? []
    : stage.agent?.approved_command_prefixes ?? inheritedCommandPrefixes;
  const commandPrefixes = brief.approved_command_prefixes ?? templateCommandPrefixes;
  const hasWorkOverrides = Boolean(
    brief.note.trim()
      || brief.context.length
      || brief.agent
      || brief.review_gate
      || brief.approved_command_prefixes,
  );
  return (
    <article className={`loop-brief-card${expanded ? " expanded" : ""}`}>
      <header>
        <KindTile stage={stage} />
        <strong>{stage.name}</strong>
        <span className="tag">{kindLabel(stage)}</span>
        <span className={`loop-brief-status${needsInput ? " warn" : brief.note.trim() ? " good" : ""}`}>
          {needsInput ? "needs input" : brief.note.trim() ? <><CheckIcon size={9} /> briefed</> : "optional · empty"}
        </span>
        <button type="button" className="btn icon sm ghost" onClick={onExpand} title={expanded ? "Collapse" : "Expand context"}>{expanded ? "▴" : "▾"}</button>
      </header>

      <div className="loop-brief-from-loop">
        <header><LockGlyph /> From the loop <span>— read-only · rev {loopRevision}</span></header>
        <div className="loop-brief-template">
          <code>steps/{stage.id}.md</code>
          <span>{instructionSummary(stage.instructions)}</span>
          <button type="button" onClick={onEditLoop}>view ▸</button>
        </div>
        {pinned && <div className="loop-brief-pinned"><span>{pinned}</span><em className="tag">template</em></div>}
        {expanded && (
          <ContextGroup title="Context" meta={`${stage.inputs.length} refs · read-only`}>
            {stage.inputs.map((context, index) => (
              <div className="loop-brief-context-row template" key={`${context.kind}-${index}`}>
                <CheckIcon size={10} />
                <code>{templateContextLabel(context)}</code>
                <span>{context.required ? "required" : "optional"}</span>
                <em className="tag">template</em>
              </div>
            ))}
            {stage.inputs.length === 0 && <span className="loop-brief-empty">No template context.</span>}
          </ContextGroup>
        )}
      </div>

      {baseStage ? (
        <div className="loop-brief-execution base">
          <label>Execution <span>this run · later stages inherit from here</span></label>
          <PlanningAgentControls value={agentConfig} onChange={onBaseAgent} pinned={pinnedPolicy} />
        </div>
      ) : brief.agent ? (
        <div className="loop-brief-execution override">
          <label>Execution <span>run override</span></label>
          <PlanningAgentControls
            value={overrideConfig}
            onChange={(next) => onAgent(overrideDelta(next, agentConfig))}
            pinned={pinnedPolicy}
          />
          <button type="button" onClick={() => onAgent(null)}>reset to inherit</button>
        </div>
      ) : (
        <div className="loop-brief-execution inherit">
          <span>execution · inherits first agent stage{execution ? ` · ${execution.provider} · ${execution.model}` : ""}</span>
          <button type="button" disabled={!agentConfig} onClick={() => agentConfig && onAgent({ provider: null, model: null, options: {} })}><EditIcon size={9} /> change</button>
        </div>
      )}

      {stage.review_gate && (
        <div className="loop-brief-gate">
          <span>on changes requested</span>
          <select
            disabled={stage.review_gate.locked}
            value={brief.review_gate ?? stage.review_gate.mode}
            onChange={(event) => {
              const mode = event.target.value as "automatic" | "human_check";
              onReviewGate(mode === stage.review_gate?.mode ? null : mode);
            }}
          >
            <option value="automatic">⟲ automatic</option>
            <option value="human_check">⚉ human check</option>
          </select>
          {brief.review_gate ? <em className="tag info">run override</em> : <em className="tag">template</em>}
          <small>template: {gateLabel(stage.review_gate)}</small>
        </div>
      )}

      {expanded && (
        <section className="loop-brief-commands">
          <header>
            <strong>Approved command prefixes</strong>
            <span>{brief.approved_command_prefixes == null ? (baseStage ? "loop default" : "inherited") : "this work"}</span>
            {brief.approved_command_prefixes != null && <button type="button" onClick={() => onApprovedCommandPrefixes(null)}>Reset to inherit</button>}
          </header>
          <CommandPrefixEditor prefixes={commandPrefixes} onChange={onApprovedCommandPrefixes} />
          <small>Matching commands run without pausing this stage for approval.</small>
        </section>
      )}

      <div className="loop-brief-for-work">
        <label className="loop-brief-note">
          <span><EditIcon size={10} /> For this work <em>— saved on {workSlug}, the loop is not modified</em>{stage.note_required && <b className="tag warn">required</b>}</span>
          <textarea
            rows={2}
            value={brief.note}
            onChange={(event) => onNote(event.target.value)}
            placeholder={`${stage.note_required === true ? "Required" : "Optional"} directions for ${stage.name}`}
          />
        </label>

        {!expanded ? (
          <div className="loop-brief-context-summary">
            <span>context · {stage.inputs.length} from loop · {brief.context.length} added</span>
            <button type="button" onClick={() => onAddContext("file")}>+ add for this work</button>
            <button type="button" onClick={onExpand}>expand ▸</button>
          </div>
        ) : (
          <div className="loop-brief-context-editor">
            <ContextGroup title="Context · this work" meta={`${brief.context.length} added · saved on ${workSlug}`}>
              {brief.context.map((context, index) => (
                <div className="loop-brief-context-row work" key={`${context.kind}-${index}`}>
                  {contextGlyph(context.kind)}
                  {context.kind === "url" || context.kind === "note" ? (
                    <input
                      autoFocus={!context.value}
                      value={context.value}
                      onChange={(event) => onReplaceContext(index, { ...context, value: event.target.value })}
                      placeholder={context.kind === "url" ? "https://..." : "Add a note for this stage"}
                    />
                  ) : <code>{context.value}</code>}
                  <span>{context.kind}</span>
                  <button type="button" className="btn icon sm ghost" onClick={() => onRemoveContext(index)} title="Remove">×</button>
                </div>
              ))}
              <div className="loop-brief-context-add">
                <button type="button" className="btn sm" onClick={() => onAddContext("file")}><PaperclipIcon size={10} /> File</button>
                <button type="button" className="btn sm" onClick={() => onAddContext("folder")}><FolderIcon size={10} /> Folder</button>
                <button type="button" className="btn sm" onClick={() => onAddContext("url")}><BranchIcon size={10} /> URL</button>
                <button type="button" className="btn sm" onClick={() => onAddContext("note")}><EditIcon size={10} /> Note</button>
              </div>
            </ContextGroup>
          </div>
        )}
        {hasWorkOverrides && (
          <div className="loop-brief-promote">
            <button type="button" onClick={onEditLoop}>↑ save into loop</button>
            <span>opens a forked revision; this run brief stays unchanged</span>
          </div>
        )}
      </div>
    </article>
  );
}

function ContextGroup({ title, meta, children }: { title: string; meta: string; children: React.ReactNode }) {
  return <section className="loop-brief-context-group"><header><strong>{title}</strong><span>{meta}</span></header>{children}</section>;
}

function StageBriefSlim({ stage }: { stage: LoopStepDefinition }) {
  const detail = stage.kind === "deterministic_check"
    ? `${stage.check_command.join(" ") || "configured check"} · no work input`
    : "waits for you · no work input";
  return <div className="loop-brief-slim"><KindTile stage={stage} /><strong>{stage.name}</strong><span className="tag">{kindLabel(stage)}</span><em>{detail}</em></div>;
}

function StageStrip({ stages }: { stages: LoopStepDefinition[] }) {
  const gated = stages.find((stage) => stage.review_gate && stage.transitions.changes_requested);
  const target = stages.find((stage) => stage.id === gated?.transitions.changes_requested);
  return <div className="loop-brief-stage-strip">{stages.map((stage, index) => <span key={stage.id}>{index > 0 && <i>→</i>}<b data-stage-kind={stage.kind}>{stageGlyph(stage)} {stage.name}</b></span>)}{gated?.review_gate && target && <span className="loop-gate-edge"><ReturnIcon size={10} /> {gated.name} → {target.name}<em className={gated.review_gate.mode === "human_check" ? "warn" : ""}>{gateLabel(gated.review_gate)}</em></span>}</div>;
}

function KindTile({ stage }: { stage: LoopStepDefinition }) {
  return <span className="loop-brief-kind" data-stage-kind={stage.kind}>{stageGlyph(stage)}</span>;
}

function stageGlyph(stage: LoopStepDefinition) {
  if (stage.kind === "pr") return <BranchIcon size={11} />;
  if (stage.kind === "agent_review") return <EyeIcon size={11} />;
  if (stage.kind === "deterministic_check") return <FlaskIcon size={11} />;
  if (stage.kind === "user_approval") return <UserCheckIcon size={11} />;
  return <DocIcon size={11} />;
}

function kindLabel(stage: LoopStepDefinition): string {
  if (stage.kind === "pr") return "create pr";
  if (stage.kind === "agent_review") return "agent review";
  if (stage.kind === "deterministic_check") return "check";
  if (stage.kind === "user_approval") return "approval";
  return "agent task";
}

function emptyStageBrief(stageId: string): LoopStageBrief {
  return { stage_id: stageId, note: "", context: [] as LoopBriefContext[], agent: null as LoopBriefAgent | null, review_gate: null as "automatic" | "human_check" | null, approved_command_prefixes: null };
}

function gateLabel(gate: NonNullable<LoopStepDefinition["review_gate"]>): string {
  return gate.mode === "human_check" ? "⚉ human check" : `⟲ automatic · max ${gate.max_passes}`;
}

function stageBrief(brief: LoopBrief, stageId: string) {
  return brief.stages.find((stage) => stage.stage_id === stageId) ?? emptyStageBrief(stageId);
}

function instructionSummary(instructions: string): string {
  return instructions.split(/\n+/).map((line) => line.replace(/^#+\s*/, "").trim()).find(Boolean) || "stage instructions";
}

function templateContextLabel(context: LoopStepDefinition["inputs"][number]): string {
  return context.paths.join(", ") || context.ref || context.step || context.kind.replaceAll("_", " ");
}

function pinnedExecution(stage: LoopStepDefinition): string {
  const agent = stage.agent;
  if (!agent) return "";
  return [agent.provider, agent.model, agent.effort && `effort ${agent.effort}`, agent.fast === true ? "fast" : agent.fast === false ? "fast off" : null, agent.permissions && `${agent.permissions} only`].filter(Boolean).join(" · ");
}

function contextGlyph(kind: LoopBriefContextKind) {
  if (kind === "folder") return <FolderIcon size={10} />;
  if (kind === "note") return <EditIcon size={10} />;
  if (kind === "url") return <BranchIcon size={10} />;
  return <PaperclipIcon size={10} />;
}

function scopeLabel(definition: LoopDefinition): string {
  return definition.scope === "builtin" ? "built-in" : definition.scope;
}

function compactPath(path: string): string {
  if (path.startsWith("/Users/")) return `~/${path.split("/").slice(3).join("/")}`;
  return path;
}

function relativePath(root: string, path: string): string {
  const normalized = root.replace(/\/$/, "");
  return normalized && path.startsWith(`${normalized}/`) ? path.slice(normalized.length + 1) : path;
}

/** What this run actually changes, not a snapshot of everything.
 *
 *  A brief override outranks the loop definition permanently: it is pinned
 *  into the run and inherited by follow-ups. Recording the whole resolved
 *  config meant merely opening this control froze every inherited value —
 *  so a later edit to the definition's effort could never take effect,
 *  because setup had silently claimed it. Only genuine differences are an
 *  instruction; the rest should keep deferring to the definition.
 */
/** Provider defaults for whatever the brief already names, so the controls
 *  render effective values while the brief itself stays sparse. */
function defaultsFor(
  descriptors: ProviderDescriptor[] | null,
  agent: LoopBriefAgent | null,
): PlanningAgentConfig | null {
  if (!agent?.provider || !agent.model || !descriptors) return null;
  const descriptor = descriptors.find((item) => item.name === agent.provider);
  if (!descriptor) return null;
  return {
    provider: descriptor.name,
    model: agent.model,
    options: providerDefaults(descriptor, agent.model),
  };
}


/** Options already pinned on the brief's entry stage. */
function entryAgentOptions(
  brief: LoopBrief,
  definition: LoopDefinition | null,
): Record<string, string> {
  const first = definition?.stages.find((stage) => stage.agent !== null) ?? null;
  if (!first) return {};
  return stageBrief(brief, first.id).agent?.options ?? {};
}




function completeAgent(
  agent: LoopBriefAgent | null,
  fallback: PlanningAgentConfig | null,
): PlanningAgentConfig | null {
  if (!agent) return null;
  const provider = agent.provider ?? fallback?.provider;
  const model = agent.model ?? fallback?.model;
  return provider && model
    ? { provider, model, options: { ...(fallback?.options ?? {}), ...agent.options } }
    : null;
}

function LockGlyph() {
  return <span aria-hidden>⚿</span>;
}
