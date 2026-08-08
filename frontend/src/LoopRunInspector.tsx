import type {
  AgentSummary,
  LoopBrief,
  LoopDefinitionSnapshot,
  LoopStepDefinition,
  PlanLoopStageRun,
} from "./api";
import {
  AlertIcon,
  BranchIcon,
  CheckIcon,
  EyeIcon,
  FlaskIcon,
  LoopIcon,
  ShieldIcon,
  SparkIcon,
  UserCheckIcon,
} from "./Icons";

type Props = {
  agent: AgentSummary | null;
  baseAgent: AgentSummary | null;
  brief: LoopBrief | null;
  definition: LoopDefinitionSnapshot | null;
  onClose: () => void;
  onSelectStage: (stageId: string) => void;
  selectedStageId: string | null;
  stages: PlanLoopStageRun[];
};

/** Read-only view of the exact loop definition pinned to a run. */
export function LoopRunInspector({
  agent,
  baseAgent,
  brief,
  definition,
  onClose,
  onSelectStage,
  selectedStageId,
  stages,
}: Props) {
  const selectedRunStage =
    stages.find((stage) => stage.id === selectedStageId) ?? stages[0] ?? null;
  const selectedDefinition = definition?.stages.find(
    (stage) => stage.id === selectedRunStage?.id,
  ) ?? null;
  const selectedBrief = brief?.stages.find(
    (stage) => stage.stage_id === selectedRunStage?.id,
  ) ?? null;
  const policy = selectedDefinition?.agent;
  const runOverride = selectedBrief?.agent;
  const approvedPrefixes = effectiveApprovedPrefixes(
    definition,
    brief,
    selectedDefinition,
    selectedRunStage?.approved_command_prefixes ?? [],
  );
  const effectiveAgent = agent ?? baseAgent;
  const provider = agent?.provider ?? runOverride?.provider ?? policy?.provider ?? baseAgent?.provider;
  const model = agent?.model ?? runOverride?.model ?? policy?.model ?? baseAgent?.model;
  const overrideOptions = optionValues(runOverride?.options);
  const policyOptions = [
    policy?.effort && `effort ${policy.effort}`,
    policy?.fast === true ? "fast" : policy?.fast === false ? "fast off" : null,
  ].filter((value): value is string => Boolean(value));
  const execution = agent
    ? executionOptions(agent)
    : overrideOptions.length > 0
      ? overrideOptions.join(" · ")
      : policyOptions.length > 0
        ? policyOptions.join(" · ")
        : baseAgent
          ? executionOptions(baseAgent)
          : "provider defaults";
  const executionSource = runOverride
    ? "run override"
    : policy?.provider || policy?.model || policy?.effort || policy?.fast != null
      ? "template"
      : effectiveAgent
        ? "inherited"
        : null;
  const legacyReviewReturn = selectedDefinition?.kind === "agent_review"
    && Boolean(selectedDefinition.transitions.changes_requested);
  const resolvedGate = selectedDefinition?.review_gate
    ? {
        mode: selectedDefinition.review_gate.locked
          ? selectedDefinition.review_gate.mode
          : selectedBrief?.review_gate ?? selectedDefinition.review_gate.mode,
        source: !selectedDefinition.review_gate.locked && selectedBrief?.review_gate
          ? "run override"
          : "template",
        maxPasses: selectedDefinition.review_gate.max_passes,
      }
    : legacyReviewReturn
      ? { mode: "automatic" as const, source: "legacy revision", maxPasses: null }
      : null;

  return (
    <aside className="run-loop-inspector" aria-label="Run loop configuration">
      <header className="run-loop-inspector-head">
        <div className="run-loop-inspector-title">
          <LoopIcon size={14} />
          <strong>{definition?.name ?? "Pinned loop"}</strong>
          {definition?.scope && <span className="tag good">{definition.scope}</span>}
          <span className="tag">read-only</span>
        </div>
        <button
          type="button"
          className="btn icon sm ghost"
          aria-label="Close loop configuration"
          title="Close loop configuration"
          onClick={onClose}
        >
          ×
        </button>
        <div className="run-loop-inspector-meta">
          <span>{stages.length} stages</span>
          <span>rev {definition?.revision ?? "legacy"} · pinned at start</span>
          <span>edits apply to future runs</span>
        </div>
      </header>

      <nav className="run-loop-inspector-nav" aria-label="Loop stages">
        {stages.map((stage) => (
          <button
            type="button"
            className={stage.id === selectedRunStage?.id ? "active" : ""}
            data-stage-kind={stage.kind}
            key={stage.id}
            onClick={() => onSelectStage(stage.id)}
          >
            <span className="run-stage-kind-tile">{stageIcon(stage)}</span>
            <span>{stage.name}</span>
            <StageState stage={stage} />
          </button>
        ))}
      </nav>

      {selectedRunStage ? (
        <div className="run-loop-inspector-body themed-scrollbar">
          <div className="run-loop-inspector-stage" data-stage-kind={selectedRunStage.kind}>
            <span className="run-stage-kind-tile">{stageIcon(selectedRunStage)}</span>
            <strong>{selectedRunStage.name}</strong>
            <span className="tag">{kindLabel(selectedRunStage.kind)}</span>
            <StageState stage={selectedRunStage} />
          </div>
          <InspectorField label="Execution · this run">
            {effectiveAgent || selectedDefinition?.agent ? (
              <div className="run-loop-inspector-lines">
                <span>
                  {provider ?? "provider unavailable"} · {model ?? "model unavailable"}
                  {executionSource && <em className={executionSource === "run override" ? "tag info" : "tag"}>{executionSource}</em>}
                </span>
                <span>
                  {execution}
                  {executionSource && <em className={executionSource === "run override" ? "tag info" : "tag"}>{executionSource}</em>}
                </span>
                <span>
                  {selectedRunStage.session ?? "no session"} · {selectedRunStage.permissions ?? "inherited permissions"}
                  {policy && <em className="tag">template</em>}
                </span>
              </div>
            ) : (
              <span className="dim">No agent executes this stage.</span>
            )}
          </InspectorField>
          {selectedDefinition?.agent && (
            <InspectorField label="Approved command prefixes">
              {approvedPrefixes.length > 0 ? (
                <div className="run-loop-inspector-lines">
                  {approvedPrefixes.map((prefix) => <code key={prefix}>$ {prefix}</code>)}
                </div>
              ) : <span className="dim">Every command asks for approval.</span>}
            </InspectorField>
          )}
          <InspectorField label="Instructions">
            <p className="run-loop-inspector-copy">
              {selectedDefinition?.instructions || "No instructions are stored for this legacy stage."}
            </p>
            {selectedBrief?.note && <p className="run-loop-inspector-copy brief"><em className="tag info">brief</em>{selectedBrief.note}</p>}
          </InspectorField>
          <InspectorField label={`Context · ${(selectedDefinition?.inputs.length ?? 0) + (selectedBrief?.context.length ?? 0)} refs`}>
            <ContextRows definition={selectedDefinition} run={selectedRunStage} workContext={selectedBrief?.context ?? []} />
          </InspectorField>
          {selectedDefinition?.kind === "deterministic_check" && (
            <InspectorField label="Check">
              <div className="run-loop-inspector-lines">
                <code>{selectedDefinition.check_command.join(" ") || "No command configured"}</code>
                <span>{selectedDefinition.retry.max_attempts} attempts · {selectedDefinition.retry.timeout_minutes} min timeout</span>
              </div>
            </InspectorField>
          )}
          {selectedDefinition?.kind === "pr" && selectedDefinition.pr_config && (
            <InspectorField label="Pull request">
              <div className="run-loop-inspector-lines">
                <span>{selectedDefinition.pr_config.name_template}</span>
                <span>{selectedDefinition.pr_config.description_mode} description · {selectedDefinition.pr_config.status}</span>
                <span>base {selectedDefinition.pr_config.base_branch}{selectedDefinition.pr_config.branch_name ? ` · branch ${selectedDefinition.pr_config.branch_name}` : ""}</span>
              </div>
            </InspectorField>
          )}
          <InspectorField label="Transitions">
            <div className="run-loop-inspector-outcomes">
              {Object.entries(selectedDefinition?.transitions ?? {}).map(([outcome, target]) => (
                <span className={outcome} key={outcome}>
                  <i /> {outcome.replaceAll("_", " ")} → {target ?? "end run"}
                </span>
              ))}
              {!selectedDefinition && <span className="dim">Pinned transitions are unavailable for this legacy run.</span>}
              {resolvedGate && (
                <span className="review-gate">
                  <i /> gate → {resolvedGate.mode === "human_check" ? "human check" : resolvedGate.maxPasses ? `automatic · max ${resolvedGate.maxPasses}` : "automatic · unbounded"}
                  <em className={resolvedGate.source === "run override" ? "tag info" : "tag"}>{resolvedGate.source}</em>
                </span>
              )}
            </div>
          </InspectorField>
        </div>
      ) : (
        <div className="run-loop-inspector-empty">No stages were recorded.</div>
      )}
    </aside>
  );
}

function InspectorField({ label, children }: { label: string; children: React.ReactNode }) {
  return <section className="run-loop-inspector-field"><header>{label}</header>{children}</section>;
}

function ContextRows({
  definition,
  run,
  workContext,
}: {
  definition: LoopStepDefinition | null;
  run: PlanLoopStageRun;
  workContext: NonNullable<LoopBrief["stages"][number]>["context"];
}) {
  if ((!definition || definition.inputs.length === 0) && workContext.length === 0) {
    return <span className="dim">No stage context configured.</span>;
  }
  return (
    <div className="run-loop-inspector-context">
      {(definition?.inputs ?? []).map((context, index) => {
        const label = context.paths.join(", ") || context.ref || context.step || context.kind;
        const warned = run.context_warnings.some((warning) => warning.includes(label));
        return (
          <span className={warned ? "warn" : ""} key={`${context.kind}-${label}-${index}`}>
            {warned ? <AlertIcon size={11} /> : <CheckIcon size={11} />}
            <span>{label}</span>
            <em>{context.required ? "required" : "optional"}{warned ? " · not found" : ""}</em>
          </span>
        );
      })}
      {workContext.map((context, index) => (
        <span key={`work-${context.kind}-${index}`}>
          <CheckIcon size={11} />
          <span>{context.value}</span>
          <em>{context.kind} <b className="tag info">work</b></em>
        </span>
      ))}
    </div>
  );
}

function StageState({ stage }: { stage: PlanLoopStageRun }) {
  const label = stage.status === "changes_requested" ? "changes" : stage.status;
  return <em className={`run-loop-stage-state ${stageTone(stage.status)}`}>{label}</em>;
}

function stageIcon(stage: Pick<PlanLoopStageRun, "id" | "kind">) {
  if (stage.kind === "pr") return <BranchIcon size={12} />;
  if (stage.kind === "agent_review") return stage.id.includes("security") ? <ShieldIcon size={12} /> : <EyeIcon size={12} />;
  if (stage.kind === "user_approval") return <UserCheckIcon size={12} />;
  if (stage.kind === "deterministic_check") return <FlaskIcon size={12} />;
  return <SparkIcon size={12} />;
}

function stageTone(status: PlanLoopStageRun["status"]): string {
  if (status === "passed") return "good";
  if (status === "running") return "info";
  if (status === "changes_requested") return "warn";
  if (status === "blocked_user") return "warn";
  if (status === "failed" || status === "cancelled") return "danger";
  return "dim";
}

function kindLabel(kind: PlanLoopStageRun["kind"]): string {
  if (kind === "pr") return "create pr";
  if (kind === "agent_task") return "agent task";
  if (kind === "agent_review") return "agent review";
  if (kind === "deterministic_check") return "check";
  return "approval";
}

function executionOptions(agent: AgentSummary): string {
  const values = optionValues(agent.options);
  return values.join(" · ") || "provider defaults";
}

function optionValues(options: Record<string, unknown> | null | undefined): string[] {
  return Object.entries(options ?? {})
    .filter(([key]) => ["effort", "reasoning_effort", "thinking_effort", "mode", "permission_mode"].includes(key))
    .map(([key, value]) => `${key.replaceAll("_", " ")} ${String(value)}`);
}

function effectiveApprovedPrefixes(
  definition: LoopDefinitionSnapshot | null,
  brief: LoopBrief | null,
  stage: LoopStepDefinition | null,
  learned: string[],
): string[] {
  if (!definition || !stage?.agent) return learned;
  const first = definition.stages.find((item) => item.agent !== null) ?? null;
  if (!first?.agent) return learned;
  const firstBrief = brief?.stages.find((item) => item.stage_id === first.id);
  const base = firstBrief?.approved_command_prefixes
    ?? first.agent.approved_command_prefixes
    ?? [];
  const stageBrief = brief?.stages.find((item) => item.stage_id === stage.id);
  const configured = stage.id === first.id
    ? base
    : stageBrief?.approved_command_prefixes
      ?? stage.agent.approved_command_prefixes
      ?? base;
  return [...new Set([...configured, ...learned])];
}
