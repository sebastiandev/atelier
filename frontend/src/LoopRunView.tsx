import { type CSSProperties, type ReactNode, useEffect, useRef, useState } from "react";

import {
  type AgentSummary,
  type ChatSummary,
  type LoopBrief,
  type LoopDefinitionSnapshot,
  type LoopRunKind,
  type LoopReviewGateState,
  type LoopStatus,
  type PlanArtifact,
  type PlanArtifactRun,
  type PlanLoopStageRun,
  type PrComment,
  type PrConfig,
  type PrLifecycle,
  type ProjectSummary,
  type RetryStageOverride,
  type WorkSummary,
  createChat,
  listAgents,
  openAgentInConsole,
  LoopFeedbackRecord,
} from "./api";
import { ChatTile } from "./Chat";
import { CreatePrDialog } from "./CreatePrDialog";
import {
  AlertIcon,
  BranchIcon,
  ChatIcon,
  CheckIcon,
  DocIcon,
  EditIcon,
  EyeIcon,
  FlaskIcon,
  LoopIcon,
  ReturnIcon,
  ShieldIcon,
  SparkIcon,
  UserCheckIcon,
} from "./Icons";
import { LoopRunInspector } from "./LoopRunInspector";
import { PaneResizeHandle } from "./PaneResizeHandle";
import {
  modelPickerOptions,
  optionLabel,
  providerEffortOption,
  useProviderDescriptors,
} from "./providerDescriptors";
import { PermissionApprovalDialog } from "./PermissionApprovalDialog";
import { PrLifecyclePanel } from "./PrLifecyclePanel";
import { RunLiveActivity, RunTranscriptDock } from "./RunAgentStream";
import { useArtifactsRefresh } from "./state/artifactsRefresh";
import {
  LOOP_INSPECTOR_MAX,
  LOOP_INSPECTOR_MIN,
  useLayoutStore,
} from "./state/layout";
import { editorUrl, useSettingsStore } from "./state/settings";
import { type AgentEvent, useAgentStream } from "./useAgentStream";

export type RunDock =
  | { kind: "loop"; stageId: string | null }
  | { kind: "transcript"; agentSlug: string; endSeq: number | null; startSeq: number; title: string }
  | { kind: "discussion"; chat: ChatSummary }
  | null;

export type RunSurfaceData = {
  accepted: boolean;
  changedFiles: Array<{ path: string; additions: number; deletions: number }>;
  cleanupAt: string | null;
  completedAt: string | null;
  costUsd: number | null;
  currentStageId: string | null;
  definition: LoopDefinitionSnapshot | null;
  elapsedSeconds: number | null;
  evidence: string[];
  goal: string;
  id: string;
  loopName: string;
  number: number;
  passNumber: number;
  passes: Array<Record<string, unknown>>;
  pr: PrLifecycle | null;
  prComments: PrComment[];
  revision: string;
  stages: PlanLoopStageRun[];
  startedAt: string;
  status: LoopStatus;
  statusReason: string;
  summary: string;
  targetId: string;
  workspacePath: string;
  brief: LoopBrief | null;
  runKind: LoopRunKind;
  seedLabel: string;
  reviewGate: LoopReviewGateState | null;
  waivedFindingsCount: number;
  waivedFindings: string[];
  feedback: LoopFeedbackRecord[];
};

type RunStageOccurrence = PlanLoopStageRun & {
  occurrenceId: string;
  passNumber: number;
  recordedAt: string;
  sourceStageId: string;
  transcriptEndSeq: number | null;
  reviewDecision?: NonNullable<PlanLoopStageRun["reports"]>[number]["review_decision"];
};

type RunSurfaceProps = {
  busy: boolean;
  chatProjects?: ProjectSummary[];
  chatWorks?: WorkSummary[];
  data: RunSurfaceData;
  dock: RunDock;
  error?: string | null;
  onApprove?: () => Promise<void>;
  onBack?: () => void;
  onCancel?: () => Promise<void>;
  onStopStage?: () => Promise<void>;
  onCreatePr?: (setup: PrConfig) => Promise<void>;
  onChangeLoop?: () => Promise<void>;
  onDock: (dock: RunDock) => void;
  onEditLoop?: () => void;
  onFollowUp?: (kind: Exclude<LoopRunKind, "initial">, note: string) => Promise<void>;
  onRequestChanges?: (note: string) => Promise<void>;
  onRefreshPr?: (force?: boolean) => Promise<void>;
  onResolveBlocker?: (note: string, agentSlug: string | null) => Promise<void> | void;
  onResolveReviewGate?: (decision: "send_back" | "approve_as_is", enforcedFindings: number[], instruction: string) => Promise<void> | void;
  onRerun?: () => Promise<void>;
  onRetry?: (override?: RetryStageOverride) => Promise<void>;
  onSendPrFeedback?: (
    comments: Array<{ comment_id: string; instruction: string }>,
    instruction: string,
  ) => Promise<void>;
  readOnly?: boolean;
  variant: "loop" | "planning";
  workSlug: string;
};

/** The failed stage agent's config, seeding the retry model/effort picker. */
type RetrySeed = {
  provider: string;
  model: string;
  options: Record<string, string>;
};

/** Shared staged-run surface used by standalone Loop and Planning story runs. */
export function RunSurface(props: RunSurfaceProps) {
  const stage = outputStage(props.data);
  const agentStage = stage?.agent_slug
    ? stage
    : [...props.data.stages].reverse().find((item) => item.agent_slug) ?? null;
  if (agentStage?.agent_slug) {
    return <StreamedRunSurface key={agentStage.agent_slug} {...props} agentStage={agentStage} />;
  }
  return <RunSurfaceContent {...props} agentStage={agentStage} events={[]} pendingPermissions={[]} sendPermission={() => {}} streamStatus="stopped" />;
}

function StreamedRunSurface(
  props: RunSurfaceProps & { agentStage: PlanLoopStageRun },
) {
  const stream = useAgentStream(props.agentStage.agent_slug!, {
    readOnly: props.readOnly,
  });
  return (
    <RunSurfaceContent
      {...props}
      events={stream.events}
      pendingPermissions={stream.pendingPermissions}
      sendPermission={stream.sendPermission}
      streamStatus={stream.status}
    />
  );
}

function RunSurfaceContent({
  agentStage,
  busy,
  chatProjects = [],
  chatWorks = [],
  data,
  dock,
  error,
  events,
  onApprove,
  onBack,
  onCancel,
  onStopStage,
  onCreatePr,
  onChangeLoop,
  onDock,
  onEditLoop,
  onFollowUp,
  onRequestChanges,
  onRefreshPr,
  onResolveBlocker,
  onResolveReviewGate,
  onRerun,
  onRetry,
  onSendPrFeedback,
  pendingPermissions,
  readOnly = false,
  sendPermission,
  streamStatus,
  variant,
  workSlug,
}: RunSurfaceProps & {
  agentStage: PlanLoopStageRun | null;
  events: AgentEvent[];
  pendingPermissions: ReturnType<typeof useAgentStream>["pendingPermissions"];
  sendPermission: ReturnType<typeof useAgentStream>["sendPermission"];
  streamStatus: ReturnType<typeof useAgentStream>["status"];
}) {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [resolution, setResolution] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [changeNote, setChangeNote] = useState("");
  const [discussionBusy, setDiscussionBusy] = useState(false);
  const [discussionError, setDiscussionError] = useState<string | null>(null);
  const [createPrOpen, setCreatePrOpen] = useState(false);
  const [selectedOccurrenceId, setSelectedOccurrenceId] = useState<string | null>(null);
  const lastArtifactSeqRef = useRef(0);
  const inspectorWidth = useLayoutStore((state) => state.loopInspectorWidth);
  const setInspectorWidth = useLayoutStore((state) => state.setLoopInspectorWidth);
  const editor = useSettingsStore((state) => state.editor);
  const terminal = useSettingsStore((state) => state.terminal);
  const stage = outputStage(data);
  const occurrences = stageOccurrences(data);
  const defaultOccurrence = defaultOutputOccurrence(occurrences, data.currentStageId);
  const selectedOccurrence = occurrences.find((item) => item.occurrenceId === selectedOccurrenceId)
    ?? defaultOccurrence;
  const currentOccurrence = [...occurrences]
    .reverse()
    .find((item) => item.sourceStageId === data.currentStageId) ?? null;
  const terminalOccurrence = currentOccurrence ?? occurrences.at(-1) ?? null;
  const currentElapsed = currentOccurrence?.agent_slug
    ? occurrenceElapsed(currentOccurrence, occurrences, events)
    : data.elapsedSeconds;
  const showingCurrentOccurrence = selectedOccurrence === null
    || currentOccurrence === null
    || selectedOccurrence.occurrenceId === currentOccurrence.occurrenceId;
  const showingTerminalOccurrence = terminalOccurrence !== null
    && (selectedOccurrenceId === null
      || selectedOccurrence?.occurrenceId === terminalOccurrence.occurrenceId);
  const latestPrOccurrence = [...occurrences].reverse().find((item) => item.kind === "pr") ?? null;
  // The approval stage is a human gate and never files a report of its own, so
  // the result panel shows the last stage that actually produced one.
  const resultOccurrence = [...occurrences]
    .reverse()
    .find((item) => item.kind !== "user_approval" && Boolean(item.summary)) ?? null;
  const finalApprovalStageId = [...(data.definition?.stages ?? [])]
    .reverse()
    .find((item) => item.kind === "user_approval")?.id;
  const waitingPermission = pendingPermissions.length > 0;
  const resultReady = ["completed", "awaiting_approval", "accepted", "cleaned"].includes(data.status);
  const canSendPrFeedback = resultReady ? onSendPrFeedback : undefined;
  const openCreatePr = data.accepted
    && onCreatePr
    && !data.definition?.stages.some((item) => item.kind === "pr")
    ? () => setCreatePrOpen(true)
    : undefined;
  const agentSlugs = occurrences.map((item) => item.agent_slug).filter(Boolean).join(":");

  useEffect(() => {
    let highest = lastArtifactSeqRef.current;
    let recorded = false;
    for (const event of events) {
      if (event.type === "artifact_recorded" && event.seq > lastArtifactSeqRef.current) {
        recorded = true;
        highest = Math.max(highest, event.seq);
      }
    }
    if (recorded) {
      lastArtifactSeqRef.current = highest;
      useArtifactsRefresh.getState().bump(workSlug);
    }
  }, [events, workSlug]);

  useEffect(() => {
    setSelectedOccurrenceId(null);
    setDiscussionError(null);
  }, [data.currentStageId, data.id, occurrences.length]);

  useEffect(() => {
    if (!agentSlugs) {
      setAgents([]);
      return;
    }
    let cancelled = false;
    listAgents(workSlug)
      .then((items) => {
        if (!cancelled) setAgents(items);
      })
      .catch(() => {
        if (!cancelled) setAgents([]);
      });
    return () => {
      cancelled = true;
    };
  }, [agentSlugs, workSlug]);

  const selectedInspectorStage = dock?.kind === "loop"
    ? dock.stageId ?? data.currentStageId ?? stage?.id ?? data.stages[0]?.id ?? null
    : null;
  const agent = agents.find((item) => item.slug === agentStage?.agent_slug) ?? null;
  // Create PR copies the *first* agent_task stage's agent (pr_lifecycle.py:164),
  // not whichever stage ran last. Seed the dialog from the same one so what it
  // shows as inherited is what the run will actually use.
  const prSourceStage = data.stages.find((item) => item.kind === "agent_task") ?? null;
  const prSourceAgent = agents.find((item) => item.slug === prSourceStage?.agent_slug) ?? null;
  const outputAgent = agents.find((item) => item.slug === selectedOccurrence?.agent_slug) ?? null;
  const discussionStage = selectedOccurrence?.agent_slug ? selectedOccurrence : agentStage;
  const discussionAgent = agents.find((item) => item.slug === discussionStage?.agent_slug) ?? null;
  const inspectorStage = data.stages.find((item) => item.id === selectedInspectorStage) ?? null;
  const inspectorAgent = agents.find((item) => item.slug === inspectorStage?.agent_slug) ?? null;
  const workspacePath = data.workspacePath || agent?.worktree_path || "";
  // Seeds the retry model/effort picker from the agent that actually ran the
  // failed stage — more accurate than the run-level provider/model, and it
  // works the same for standalone Loop and Planning runs.
  const retryAgent = agents.find((item) => item.slug === stage?.agent_slug) ?? null;
  const retrySeed: RetrySeed | null = retryAgent
    ? {
        provider: retryAgent.provider,
        model: retryAgent.model,
        options: Object.fromEntries(
          Object.entries(retryAgent.options ?? {}).map(([key, value]) => [key, String(value)]),
        ),
      }
    : null;

  async function sendChangeRequest() {
    const note = changeNote.trim();
    if (!onRequestChanges || !note) return;
    try {
      await onRequestChanges(note);
    } catch {
      return;
    }
    setChangeNote("");
    setRequesting(false);
  }

  async function openDiscussion(
    discussionStage: PlanLoopStageRun,
    discussionAgent: AgentSummary,
  ) {
    if (discussionBusy) return;
    setDiscussionBusy(true);
    setDiscussionError(null);
    try {
      const chat = await createChat({
        provider: discussionAgent.provider,
        model: discussionAgent.model,
        title: discussionStage.name,
        grounding: { kind: "work", ref: workSlug },
        working_directory: data.workspacePath || discussionAgent.worktree_path || null,
        options: stringOptions(discussionAgent.options ?? {}),
        discussion_only: true,
        role: "advisory",
        discussion_key: JSON.stringify([
          workSlug,
          data.targetId,
          data.id,
          discussionStage.id,
        ]),
        context_seed: buildRunDiscussionSeed(data, discussionStage),
      });
      onDock({ kind: "discussion", chat });
    } catch (reason) {
      setDiscussionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDiscussionBusy(false);
    }
  }

  function selectStageOutput(stageId: string) {
    const occurrence = [...occurrences]
      .reverse()
      .find((item) => item.sourceStageId === stageId);
    if (occurrence) setSelectedOccurrenceId(occurrence.occurrenceId);
    onDock({ kind: "loop", stageId });
  }

  return (
    <div
      className={`run-surface${dock ? " with-dock" : ""}`}
      style={{ "--run-dock-width": `${inspectorWidth}px` } as CSSProperties}
    >
      <div className="run-surface-main">
        <RunHeader
          data={data}
          onBack={variant === "planning" ? onBack : undefined}
          waitingPermission={waitingPermission}
          workspacePath={workspacePath}
          onViewLoop={() => onDock({ kind: "loop", stageId: data.currentStageId })}
        />
        <LoopRunStageSpine
          passes={data.passes}
          runStartedAt={data.startedAt}
          stages={occurrences}
          selectedOccurrenceId={selectedOccurrence?.occurrenceId ?? null}
          trailing={<>{formatElapsed(currentElapsed)}{data.costUsd !== null && <> · ${data.costUsd.toFixed(2)}</>}</>}
          onStage={(occurrenceId) => setSelectedOccurrenceId((selected) => (
            selected === occurrenceId ? null : occurrenceId
          ))}
          gateLabel={runGateLabel(data)}
        />

        <div className="run-surface-scroll themed-scrollbar">
          <div className="run-surface-wrap">
            {showingCurrentOccurrence && waitingPermission && (
              <PermissionApprovalDialog
                pendingPermissions={pendingPermissions}
                onDecide={sendPermission}
              />
            )}
            {showingCurrentOccurrence && data.status === "blocked_user" && data.reviewGate && (
              <ReviewGateDecision
                key={`${data.id}:${data.reviewGate.stage_id}:${data.reviewGate.passes_used}`}
                busy={busy}
                gate={data.reviewGate}
                onResolve={onResolveReviewGate}
              />
            )}
            {showingCurrentOccurrence && data.status === "blocked_user" && !data.reviewGate && (
              <div className="run-state-card blocked">
                <AlertIcon size={15} />
                <div>
                  <strong>Blocked · the agent needs a decision</strong>
                  <p>{stage?.blocker || data.statusReason || "Resolve the blocker, then resume this stage."}</p>
                  {onResolveBlocker && <><input
                      value={resolution}
                      onChange={(event) => setResolution(event.target.value)}
                      placeholder="What changed or was decided?"
                    />
                    <div className="run-state-actions">
                      <button className="btn primary sm" disabled={busy || !resolution.trim()} onClick={() => void onResolveBlocker(resolution.trim(), stage?.agent_slug ?? null)}><CheckIcon size={11} /> Resume with answer</button>
                      <button className="btn sm" disabled={busy} onClick={() => void onResolveBlocker("Fold this into the current scope and continue.", stage?.agent_slug ?? null)}>Fold it in</button>
                      <button className="btn sm" disabled={busy} onClick={() => void onResolveBlocker("Keep this out of scope and continue.", stage?.agent_slug ?? null)}>Keep it out</button>
                      <button className="btn chat sm" disabled={!agentStage || !agent || discussionBusy} onClick={() => agentStage && agent && void openDiscussion(agentStage, agent)}><ChatIcon size={11} /> Answer in chat</button>
                    </div></>}
                </div>
              </div>
            )}
            {showingCurrentOccurrence && data.status === "waiting_report" && (
              <div className="run-state-card waiting">
                <AlertIcon size={15} />
                <div>
                  <strong>No agent activity for five minutes</strong>
                  <p>{data.statusReason}</p>
                  {onRetry && (
                    <div className="run-state-actions">
                      <button className="btn primary sm" disabled={busy} onClick={() => void onRetry()}>
                        <LoopIcon size={11} /> Retry stage
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}

            {(data.status === "failed" || data.status === "cancelled") && showingCurrentOccurrence ? (
              <>
                <FailureView
                  data={data}
                  events={events}
                  busy={busy}
                  stage={stage}
                  onRetry={onRetry}
                  retrySeed={retrySeed ?? undefined}
                  onChangeLoop={onChangeLoop}
                  onRerun={onRerun}
                  onEditLoop={onEditLoop}
                  onSendToImplement={stage?.kind === "pr" && onSendPrFeedback
                    ? () => onSendPrFeedback([], `Resolve the Create PR failure: ${data.statusReason}`)
                    : undefined}
                  onTranscript={() => selectedOccurrence?.agent_slug && onDock(
                    transcriptDock(selectedOccurrence, occurrences),
                  )}
                />
                {selectedOccurrenceId !== null && selectedOccurrence && (
                  <StageOutput
                    agent={outputAgent}
                    busy={busy}
                    definition={data.definition}
                    feedback={data.feedback}
                    events={selectedOccurrence.occurrenceId === currentOccurrence?.occurrenceId ? events : []}
                    pr={data.pr}
                    prComments={data.prComments}
                    runStatus={data.status}
                    stage={selectedOccurrence}
                    onRefreshPr={selectedOccurrence.occurrenceId === latestPrOccurrence?.occurrenceId ? onRefreshPr : undefined}
                    onSendPrFeedback={selectedOccurrence.occurrenceId === latestPrOccurrence?.occurrenceId ? canSendPrFeedback : undefined}
                    onConfig={() => onDock({ kind: "loop", stageId: selectedOccurrence.sourceStageId })}
                  />
                )}
              </>
            ) : resultReady && showingTerminalOccurrence && selectedOccurrence?.kind !== "pr" ? (
              <ResultView
                busy={busy}
                data={data}
                stage={resultOccurrence}
                onCreatePr={openCreatePr}
              />
            ) : selectedOccurrence ? (
              <StageOutput
                feedback={data.feedback}
                agent={outputAgent}
                busy={busy}
                definition={data.definition}
                events={selectedOccurrence.occurrenceId === currentOccurrence?.occurrenceId ? events : []}
                pr={data.pr}
                prComments={data.prComments}
                runStatus={data.status}
                stage={selectedOccurrence}
                onRefreshPr={selectedOccurrence.occurrenceId === latestPrOccurrence?.occurrenceId ? onRefreshPr : undefined}
                onSendPrFeedback={selectedOccurrence.occurrenceId === latestPrOccurrence?.occurrenceId ? canSendPrFeedback : undefined}
                onConfig={() => onDock({ kind: "loop", stageId: selectedOccurrence.sourceStageId })}
              />
            ) : (
              <div className="run-empty-state">Waiting for the first stage…</div>
            )}

            {data.accepted
              && selectedOccurrence?.sourceStageId === finalApprovalStageId
              && onFollowUp && (
              <FollowUpChooser
                busy={busy}
                canVerify={Boolean(data.definition?.stages.some((item) =>
                  item.kind === "agent_review" || item.kind === "deterministic_check"
                ))}
                onStart={onFollowUp}
              />
            )}

            {requesting && showingTerminalOccurrence && onRequestChanges && !data.accepted && (
              <div className="run-change-request">
                <strong>Request changes</strong>
                <textarea value={changeNote} onChange={(event) => setChangeNote(event.target.value)} placeholder="Describe what needs to change…" autoFocus />
                <div><button className="btn sm" onClick={() => setRequesting(false)}>Cancel</button><button className="btn warn sm" disabled={busy || !changeNote.trim()} onClick={() => void sendChangeRequest()}><ReturnIcon size={11} /> Send &amp; re-run</button></div>
              </div>
            )}
            {(error || discussionError) && <div className="form-error">{error || discussionError}</div>}
          </div>
        </div>

        <DismissedFindings data={data} />
        <RunActions
          agentStage={agentStage}
          busy={busy}
          data={data}
          discussionAgent={discussionAgent}
          discussionStage={discussionStage}
          onApprove={showingTerminalOccurrence ? onApprove : undefined}
          onCancel={onCancel}
          onStopStage={onStopStage}
          onChangeLoop={showingTerminalOccurrence ? onChangeLoop : undefined}
          discussionBusy={discussionBusy}
          onDiscuss={(stage, stageAgent) => void openDiscussion(stage, stageAgent)}
          onEditLoop={showingTerminalOccurrence ? onEditLoop : undefined}
          onOpenEditor={!readOnly && workspacePath ? () => {
            window.location.href = editorUrl(editor, workspacePath);
          } : undefined}
          onOpenConsole={!readOnly && selectedOccurrence?.agent_slug ? () => {
            void openAgentInConsole(selectedOccurrence.agent_slug!, terminal).catch((reason) => {
              setDiscussionError(reason instanceof Error ? reason.message : String(reason));
            });
          } : undefined}
          onRequestChanges={showingTerminalOccurrence && onRequestChanges
            ? () => setRequesting((value) => !value)
            : undefined}
          onTranscript={() => selectedOccurrence?.agent_slug && onDock(
            transcriptDock(selectedOccurrence, occurrences),
          )}
        />
      </div>

      {dock && (
        <PaneResizeHandle
          defaultValue={420}
          edge="left"
          label="Resize run dock"
          max={LOOP_INSPECTOR_MAX}
          min={LOOP_INSPECTOR_MIN}
          value={inspectorWidth}
          onChange={setInspectorWidth}
        />
      )}
      {dock?.kind === "loop" && (
        <LoopRunInspector
          agent={inspectorAgent}
          baseAgent={agent}
          brief={data.brief}
          definition={data.definition}
          stages={data.stages}
          selectedStageId={selectedInspectorStage}
          onSelectStage={selectStageOutput}
          onClose={() => onDock(null)}
        />
      )}
      {dock?.kind === "transcript" && (
        <OccurrenceTranscriptDock
          agentSlug={dock.agentSlug}
          currentAgentSlug={agentStage?.agent_slug ?? null}
          currentEvents={events}
          currentStatus={streamStatus}
          endSeq={dock.endSeq}
          startSeq={dock.startSeq}
          title={dock.title}
          onClose={() => onDock(null)}
        />
      )}
      {dock?.kind === "discussion" && (
        <aside className="run-discussion-dock">
          <ChatTile
            chatSlug={dock.chat.slug}
            chatSummary={dock.chat}
            projects={chatProjects}
            works={chatWorks}
            onClose={() => onDock(null)}
          />
        </aside>
      )}
      {createPrOpen && onCreatePr && (
        <CreatePrDialog
          goal={data.goal}
          runLabel={`run ${data.number}`}
          inheritedAgent={prSourceAgent ? {
            provider: prSourceAgent.provider,
            model: prSourceAgent.model,
            options: stringOptions(prSourceAgent.options ?? {}),
          } : null}
          inheritedFrom={prSourceStage?.name ?? null}
          workspacePath={workspacePath}
          onClose={() => setCreatePrOpen(false)}
          onCreate={async (setup) => {
            await onCreatePr(setup);
            setCreatePrOpen(false);
          }}
        />
      )}
    </div>
  );
}

function OccurrenceTranscriptDock({
  agentSlug,
  currentAgentSlug,
  currentEvents,
  currentStatus,
  endSeq,
  onClose,
  startSeq,
  title,
}: {
  agentSlug: string;
  currentAgentSlug: string | null;
  currentEvents: AgentEvent[];
  currentStatus: ReturnType<typeof useAgentStream>["status"];
  endSeq: number | null;
  onClose: () => void;
  startSeq: number;
  title: string;
}) {
  if (agentSlug === currentAgentSlug) {
    return (
      <RunTranscriptDock
        agentSlug={agentSlug}
        endSeq={endSeq}
        events={currentEvents}
        startSeq={startSeq}
        status={currentStatus}
        title={title}
        onClose={onClose}
      />
    );
  }
  return (
    <HistoricalOccurrenceTranscriptDock
      key={agentSlug}
      agentSlug={agentSlug}
      endSeq={endSeq}
      startSeq={startSeq}
      title={title}
      onClose={onClose}
    />
  );
}

function HistoricalOccurrenceTranscriptDock({
  agentSlug,
  endSeq,
  onClose,
  startSeq,
  title,
}: {
  agentSlug: string;
  endSeq: number | null;
  onClose: () => void;
  startSeq: number;
  title: string;
}) {
  const stream = useAgentStream(agentSlug, { readOnly: true });
  return (
    <RunTranscriptDock
      agentSlug={agentSlug}
      endSeq={endSeq}
      events={stream.events}
      startSeq={startSeq}
      status={stream.status}
      title={title}
      onClose={onClose}
    />
  );
}

type LoopRunViewProps = {
  artifact: PlanArtifact;
  busy: boolean;
  chatProjects?: ProjectSummary[];
  chatWorks?: WorkSummary[];
  dock: RunDock;
  onApprove: () => Promise<void>;
  onBack: () => void;
  onCancel: () => Promise<void>;
  onStopStage: () => Promise<void>;
  onDock: (dock: RunDock) => void;
  onRequestChanges: (note: string) => Promise<void>;
  onRerun: () => void;
  onResolveBlocker: (note: string, agentSlug: string) => void;
  onResolveReviewGate: (decision: "send_back" | "approve_as_is", enforcedFindings: number[], instruction: string) => void;
  onRetry: (override?: RetryStageOverride) => Promise<void> | void;
  onCreatePr?: RunSurfaceProps["onCreatePr"];
  onFollowUp?: RunSurfaceProps["onFollowUp"];
  onSendPrFeedback?: RunSurfaceProps["onSendPrFeedback"];
  onRefreshPr?: RunSurfaceProps["onRefreshPr"];
  readOnly?: boolean;
  run: PlanArtifactRun;
  workSlug: string;
};

/** Planning adapter for the shared run surface. */
export function LoopRunView({ artifact, run, ...props }: LoopRunViewProps) {
  const data = planningRunData(artifact, run);
  return (
    <RunSurface
      busy={props.busy}
      chatProjects={props.chatProjects}
      chatWorks={props.chatWorks}
      data={data}
      dock={props.dock}
      variant="planning"
      workSlug={props.workSlug}
      readOnly={props.readOnly}
      onApprove={props.readOnly ? undefined : props.onApprove}
      onBack={props.onBack}
      onCancel={props.readOnly ? undefined : props.onCancel}
      onStopStage={props.readOnly ? undefined : props.onStopStage}
      onCreatePr={props.readOnly ? undefined : props.onCreatePr}
      onFollowUp={props.readOnly ? undefined : props.onFollowUp}
      onDock={props.onDock}
      onRequestChanges={props.readOnly ? undefined : props.onRequestChanges}
      onRerun={props.readOnly ? undefined : async () => props.onRerun()}
      onResolveBlocker={props.readOnly ? undefined : (note, agentSlug) => {
        if (agentSlug) props.onResolveBlocker(note, agentSlug);
      }}
      onResolveReviewGate={props.readOnly ? undefined : props.onResolveReviewGate}
      onRetry={props.readOnly ? undefined : async (override) => props.onRetry(override)}
      onRefreshPr={props.readOnly ? undefined : props.onRefreshPr}
      onSendPrFeedback={props.readOnly ? undefined : props.onSendPrFeedback}
    />
  );
}

function ReviewGateDecision({
  busy,
  gate,
  onResolve,
}: {
  busy: boolean;
  gate: LoopReviewGateState;
  onResolve?: RunSurfaceProps["onResolveReviewGate"];
}) {
  const details = gate.finding_details.length > 0
    ? gate.finding_details
    : gate.findings.map((text) => ({ text, severity: "medium" as const, location: "" }));
  const [selected, setSelected] = useState(() => new Set(details.map((_, index) => index)));
  const [instruction, setInstruction] = useState("");
  const enforced = [...selected].sort((left, right) => left - right);
  return (
    <section className="run-review-gate">
      <header>
        <span className="run-stage-kind-tile"><EyeIcon size={12} /></span>
        <strong>Code review · changes requested</strong>
        <span className="tag">{details.length} findings</span>
        <small>enforce {selected.size} of {details.length}</small>
        {gate.passes_spent && <em className="tag warn">⟲ {gate.passes_used}/{gate.max_passes} · passes spent</em>}
      </header>
      <div className="run-review-findings">
        {details.map((finding, index) => (
          <label className={selected.has(index) ? "selected" : ""} key={`${finding.text}-${index}`}>
            <input
              type="checkbox"
              disabled={!onResolve}
              checked={selected.has(index)}
              onChange={(event) => setSelected((current) => {
                const next = new Set(current);
                if (event.target.checked) next.add(index); else next.delete(index);
                return next;
              })}
            />
            <em className={`sev ${finding.severity}`}>{finding.severity}</em>
            <span>{finding.text}</span>
            {finding.location && <code>{finding.location}</code>}
          </label>
        ))}
      </div>
      {onResolve && <label className="run-review-instruction">
        <span>Custom instruction for the next pass <em>· optional · appended to the implementation brief</em></span>
        <textarea rows={2} value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="Add a focused instruction for the next pass" />
      </label>}
      {onResolve && <footer>
        <small>unchecked findings are waived and recorded on this Work</small>
        <span />
        <button className="btn" disabled={busy} onClick={() => void onResolve("send_back", enforced, instruction.trim())}><ReturnIcon size={11} /> Send back · enforce {selected.size}</button>
        <button className="btn primary" disabled={busy} onClick={() => void onResolve("approve_as_is", [], "")}><CheckIcon size={11} /> Approve as is</button>
      </footer>}
    </section>
  );
}

export function LoopRunStageSpine({
  passes,
  runStartedAt,
  stages,
  selectedOccurrenceId,
  trailing,
  onStage,
  gateLabel,
}: {
  passes?: Array<Record<string, unknown>>;
  runStartedAt?: string;
  stages: RunStageOccurrence[];
  selectedOccurrenceId?: string | null;
  trailing?: ReactNode;
  onStage?: (occurrenceId: string) => void;
  gateLabel?: string | null;
}) {
  const [expandedPasses, setExpandedPasses] = useState<Set<number>>(() => new Set());
  const passGroups = [...stages.reduce((groups, stage) => {
    const group = groups.get(stage.passNumber) ?? [];
    group.push(stage);
    groups.set(stage.passNumber, group);
    return groups;
  }, new Map<number, RunStageOccurrence[]>())].sort(([left], [right]) => left - right);
  const currentPass = passGroups.at(-1)?.[0] ?? 1;

  function stageStrip(items: RunStageOccurrence[], current: boolean) {
    return <div className="run-stage-spine">
      {items.map((stage, index) => {
        const state = stageTone(stage.status);
        const previous = items[index - 1];
        const returns = previous && stageOrder(stages, stage.sourceStageId) <= stageOrder(stages, previous.sourceStageId);
        return (
          <span className="run-stage-spine-unit" key={stage.occurrenceId}>
            {previous && <span className={`run-stage-arrow${returns ? " return" : ""}`}>{returns ? "↩" : "→"}</span>}
            <button
              type="button"
              aria-pressed={stage.occurrenceId === selectedOccurrenceId}
              className={`run-stage-chip ${state}${stage.occurrenceId === selectedOccurrenceId ? " selected" : ""}`}
              data-stage-kind={stage.kind}
              onClick={() => onStage?.(stage.occurrenceId)}
            >
              {state === "passed" ? <CheckIcon size={10} /> : state === "running" ? <span className="run-live-dot" /> : state === "changes" ? <ReturnIcon size={10} /> : state === "failed" ? <AlertIcon size={10} /> : state === "skipped" ? "⤳" : stageIcon(stage, 12)}
              {stage.name}
              {state === "skipped" && <small>· skipped</small>}
              {state === "failed" && stage.attempt > 0 && <small>· {stage.attempt} attempts</small>}
              {stage.passNumber > 1 && <small>· pass {stage.passNumber}</small>}
            </button>
          </span>
        );
      })}
      {current && gateLabel && <span className="run-stage-gate"><ReturnIcon size={10} /> changes requested <em>{gateLabel}</em></span>}
      {current && <span className="run-stage-attempt">{trailing}</span>}
    </div>;
  }

  if (passGroups.length <= 1) return stageStrip(stages, true);
  return (
    <div className="run-pass-stack">
      {passGroups.map(([passNumber, items]) => {
        const selected = items.some((stage) => stage.occurrenceId === selectedOccurrenceId);
        const current = passNumber === currentPass;
        const expanded = current || expandedPasses.has(passNumber);
        if (!expanded) {
          const reviewReturns = items.filter((stage) => stage.status === "changes_requested").length;
          const pr = [...items].reverse().find((stage) => stage.kind === "pr");
          const summary = passSummary(passes, passNumber);
          const addressed = passAddressedCount(summary, items);
          const elapsed = passElapsed(summary, items, runStartedAt);
          const passCost = numberValue(summary?.cost_usd);
          return (
            <button
              type="button"
              aria-expanded={false}
              className={`run-pass-collapsed${selected ? " selected" : ""}`}
              key={passNumber}
              onClick={() => setExpandedPasses((value) => new Set(value).add(passNumber))}
            >
              <span>▸ pass {passNumber}</span>
              <strong><CheckIcon size={10} /> {items.length} stages</strong>
              <em>{stringValue(summary?.reason) || (passNumber === 1 ? "initial" : "PR feedback")}</em>
              {reviewReturns > 0 && <em>review ⟲{reviewReturns} inside</em>}
              {pr?.pr?.number && <em>PR #{String(pr.pr.number)} {pr.pr.status}</em>}
              {addressed > 0 && <em>{addressed} comments addressed</em>}
              {elapsed !== null && <em>{formatElapsed(elapsed)}</em>}
              {passCost !== null && <em>${passCost.toFixed(2)}</em>}
            </button>
          );
        }
        return (
          <section className={`run-pass${current ? " current" : ""}${selected ? " selected" : ""}`} key={passNumber}>
            <button
              type="button"
              className="run-pass-heading"
              aria-expanded
              disabled={current}
              onClick={() => setExpandedPasses((value) => {
                const next = new Set(value);
                next.delete(passNumber);
                return next;
              })}
            >▾ pass {passNumber}<em>{current ? "current" : "previous"}</em></button>
            {stageStrip(items, current)}
          </section>
        );
      })}
    </div>
  );
}

export type RunRailRun = Pick<
  RunSurfaceData,
  "id" | "loopName" | "number" | "revision" | "runKind" | "seedLabel" | "status"
>;

export function RunRail({
  anchor,
  children,
  definition,
  definitionMeta = "current — next run uses this",
  onRun,
  onViewLoop,
  runs,
  selectedRunId,
}: {
  anchor: ReactNode;
  children?: ReactNode;
  definition: LoopDefinitionSnapshot | null;
  definitionMeta?: string;
  onRun: (runId: string) => void;
  onViewLoop: () => void;
  runs: RunRailRun[];
  selectedRunId: string | null;
}) {
  const gateStage = definition?.stages.find((stage) => stage.review_gate && stage.transitions.changes_requested);
  const gateTarget = definition?.stages.find((stage) => stage.id === gateStage?.transitions.changes_requested);
  return (
    <aside className="loop-mode-rail run-rail">
      {anchor}
      <section className="loop-mode-rail-section">
        <header><span>Loop</span></header>
        {definition ? (
          <div className="loop-mode-rail-definition">
            <strong>
              <LoopIcon size={11} />
              <span>{definition.name}</span>
              <em className={definition.scope}>{definition.scope === "builtin" ? "built-in" : definition.scope}</em>
            </strong>
            <span className="loop-mode-rail-meta">rev {definition.revision} · {definitionMeta}</span>
            <div className="loop-mode-rail-stages">
              {definition.stages.map((stage, index) => (
                <span className="loop-mode-rail-stage-unit" key={stage.id}>
                  {index > 0 && <span className="loop-mode-rail-stage-arrow">→</span>}
                  <span className="loop-mode-rail-stage" data-stage-kind={stage.kind}>
                    <i>{stageIcon(stage, 10)}</i>
                    <b>{stage.name}</b>
                  </span>
                </span>
              ))}
            </div>
            {gateStage?.review_gate && gateTarget && <div className="loop-mode-rail-gate"><ReturnIcon size={10} /> {gateStage.name} → {gateTarget.name}<em>{gateStage.review_gate.mode === "human_check" ? "⚉ human check" : `⟲ auto · max ${gateStage.review_gate.max_passes}`}</em></div>}
            <button type="button" className="loop-mode-rail-disclosure" onClick={onViewLoop}>▸ view stages &amp; config</button>
          </div>
        ) : <span className="loop-mode-rail-empty">Pinned loop details are unavailable for this legacy run.</span>}
      </section>
      <section className="loop-mode-rail-section">
        <header><span>Runs</span><em>{runs.length}</em></header>
        <div className="loop-mode-rail-section-body themed-scrollbar">
          {runs.map((run) => (
            <button
              key={run.id}
              type="button"
              className={`loop-mode-rail-row${run.id === selectedRunId ? " active" : ""}`}
              onClick={() => onRun(run.id)}
            >
              <span>run {run.number}</span>
              <strong>{run.runKind === "initial" ? `initial · rev ${run.revision || "legacy"}` : `${run.runKind} · ${run.seedLabel || "current state"}`}</strong>
              <em className={runStatusTone(run.status)}>{statusLabel(run.status)}</em>
            </button>
          ))}
        </div>
      </section>
      {children}
    </aside>
  );
}

function RunHeader({
  data,
  onBack,
  waitingPermission,
  workspacePath,
  onViewLoop,
}: {
  data: RunSurfaceData;
  onBack?: () => void;
  waitingPermission: boolean;
  workspacePath: string;
  onViewLoop: () => void;
}) {
  return (
    <header className="run-surface-head">
      <strong>run {data.number}{data.runKind !== "initial" && <> · {data.runKind}</>} · {data.loopName}</strong>
      <span>rev {data.revision || "legacy"}{data.seedLabel && <> · seeded from {data.seedLabel}</>}{workspacePath && <> · {compactPath(workspacePath)}</>}</span>
      <button type="button" onClick={onViewLoop}>view loop ▸</button>
      {onBack && <button type="button" onClick={onBack}>← back to story</button>}
      <RunStatus status={data.status} stage={outputStage(data)?.name} waitingPermission={waitingPermission} waivedFindings={data.waivedFindingsCount} />
    </header>
  );
}

function RunStatus({ status, stage, waitingPermission, waivedFindings }: { status: LoopStatus; stage?: string; waitingPermission: boolean; waivedFindings: number }) {
  const running = ["pending", "running", "waiting_report", "assessing", "needs_agent"].includes(status);
  const inactive = status === "waiting_report";
  const tone = waitingPermission
    ? "warn"
    : inactive
      ? "warn"
    : status === "failed" || status === "cancelled"
    ? "danger"
    : status === "accepted" || status === "cleaned"
      ? "good"
      : running ? "info" : "warn";
  const label = waitingPermission
    ? `permission · ${stage ?? "agent"}`
    : inactive
      ? `no activity · ${stage ?? "agent"}`
    : running && stage ? `running · ${stage}`
    : status === "accepted" && waivedFindings > 0 ? `accepted · ${waivedFindings} waived`
    : statusLabel(status);
  return <em className={`run-surface-status ${tone}`}>{waitingPermission || inactive ? <AlertIcon size={10} /> : running && <span className="run-live-dot" />}{label}</em>;
}

function StageOutput({
  agent,
  busy,
  definition,
  events,
  feedback,
  onConfig,
  onRefreshPr,
  onSendPrFeedback,
  pr,
  prComments,
  runStatus,
  stage,
}: {
  agent: AgentSummary | null;
  busy: boolean;
  definition: LoopDefinitionSnapshot | null;
  events: AgentEvent[];
  feedback: LoopFeedbackRecord[];
  onConfig: () => void;
  onRefreshPr?: (force?: boolean) => Promise<void>;
  onSendPrFeedback?: RunSurfaceProps["onSendPrFeedback"];
  pr: PrLifecycle | null;
  prComments: PrComment[];
  runStatus: LoopStatus;
  stage: RunStageOccurrence;
}) {
  const configured = definition?.stages.find((item) => item.id === stage.id) ?? null;
  const running = stage.status === "running" && ["running", "waiting_report", "assessing", "needs_agent"].includes(runStatus);
  const returnTargetId = configured?.transitions.changes_requested ?? null;
  const returnTarget = definition?.stages.find((item) => item.id === returnTargetId)?.name ?? returnTargetId;
  const updatingPr = stage.kind === "pr" && Boolean(pr?.url) && stage.passNumber > 1;
  return (
    <section className={`run-stage-output${running ? " running" : ""}`} data-stage-kind={stage.kind}>
      <header>
        <span className="run-stage-kind-tile">{stageIcon(stage, 13)}</span>
        <strong>{updatingPr ? `Update PR #${pr?.number ?? ""}` : stage.name}</strong>
        <span className="tag">{updatingPr ? "commit & push only" : kindLabel(stage.kind)}</span>
        <span className={`tag ${stageTone(stage.status)}`}>{stageStatusLabel(stage.status)}</span>
        {stage.kind === "pr" && pr && (
          /* The stage tag reports the stage lifecycle ("passed"); the PR can
             move on independently after that, so surface its outcome here
             rather than only inside the panel below. */
          <span className={`tag ${prStatusTone(pr.status)}`}>#{pr.number ?? ""} {pr.status}</span>
        )}
        <em>pass {stage.passNumber}</em>
      </header>
      {stage.kind !== "user_approval" && (
        <div className="run-stage-execution">
          <span>{agent ? `${agent.provider} · ${agent.model}` : configured?.agent?.provider || "run defaults"}</span>
          {configured?.agent?.effort && <span>effort {configured.agent.effort}</span>}
          <span>{stage.permissions ?? "inherited perms"}</span>
          <span>{stage.session ? `${stage.session} session` : "no session"}</span>
          <button type="button" onClick={onConfig}>stage config ▸</button>
        </div>
      )}
      {stage.context_warnings.length > 0 && <div className="run-stage-context-warning"><AlertIcon size={11} /> Optional context not found: {stage.context_warnings.join(", ")}</div>}
      {updatingPr && <div className="run-stage-pr-mode">Reuses this run's saved PR setup and branch. No new pull request is created.</div>}
      {stage.status === "changes_requested" && returnTarget && <div className="run-stage-return"><ReturnIcon size={11} /> loop returns to {returnTarget} · pass {stage.passNumber + 1}</div>}
      {running && stage.agent_slug && <RunLiveActivity events={events} />}
      {!running && stage.summary && <StageReport stage={stage} feedback={feedback} />}
      {stage.kind === "pr" && pr && (
        <PrLifecyclePanel
          addressedComments={stage.addressed_comments}
          busy={busy}
          pushInFlight={running}
          comments={prComments}
          feedbackInstruction={stage.feedback_instruction}
          onRefresh={onRefreshPr}
          onSendFeedback={onSendPrFeedback}
          pr={pr}
          pushAt={stage.push_at ?? null}
        />
      )}
      {!running && stage.kind === "deterministic_check" && !stage.summary && (
        <div className="run-check-output"><code>$ {configured?.check_command.join(" ") || "configured check"}</code><span>{stageStatusLabel(stage.status)}</span></div>
      )}
      {stage.status === "pending" && <div className="run-stage-pending">Runs after the previous stage passes.</div>}
      {stage.status === "skipped" && <div className="run-stage-pending">Skipped for this verification run.</div>}
      {running && <div className="runbar" />}
    </section>
  );
}

/** Retry button with an optional same-provider model/effort override. */
function RetryStageControls({
  seed,
  busy,
  onRetry,
  label,
  hint,
}: {
  seed: RetrySeed;
  busy: boolean;
  onRetry: (override?: RetryStageOverride) => Promise<void>;
  label: string;
  hint: string;
}) {
  const { byName } = useProviderDescriptors();
  const descriptor = byName?.[seed.provider] ?? null;
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState(seed.model);
  const [effort, setEffort] = useState<string | null>(null);
  const [note, setNote] = useState("");

  const effortOption = descriptor ? providerEffortOption(descriptor, model) : null;
  const effortKey = effortOption?.key ?? null;
  const seedEffort = effortKey ? seed.options[effortKey] ?? null : null;
  const effortValues = effortOption?.field.values ?? [];
  // Keep the shown effort valid for the selected model's ladder; fall back to
  // the run's current effort, then the provider default.
  const effectiveEffort =
    effort && effortValues.includes(effort)
      ? effort
      : seedEffort && effortValues.includes(seedEffort)
        ? seedEffort
        : effortOption?.field.default ?? null;

  const modelChoices = descriptor ? modelPickerOptions(descriptor) : [];
  const models = modelChoices.some((choice) => choice.value === seed.model)
    ? modelChoices
    : [{ value: seed.model, label: seed.model }, ...modelChoices];

  const modelChanged = model !== seed.model;
  const effortChanged =
    effortKey !== null && effectiveEffort !== null && effectiveEffort !== seedEffort;

  const instructed = note.trim() !== "";

  function retry() {
    const override: RetryStageOverride = {};
    if (modelChanged) override.model = model;
    if (effortChanged) override.effort = effectiveEffort;
    if (instructed) override.note = note.trim();
    void onRetry(modelChanged || effortChanged || instructed ? override : undefined);
  }

  return (
    <>
      <div className="retry-actions">
        <button className="btn primary sm" disabled={busy} onClick={retry}>
          <LoopIcon size={11} /> {label}
        </button>
        <button className="btn ghost sm" disabled={busy} onClick={() => setOpen((value) => !value)}>
          {open ? "Hide options" : descriptor ? "Add instructions / change model" : "Add instructions"}
        </button>
      </div>
      <span>{hint}</span>
      {open && (
        <div className="retry-overrides">
          <label className="retry-note">
            <span>Instructions for this attempt</span>
            <textarea
              className="input sm"
              disabled={busy}
              onChange={(event) => setNote(event.target.value)}
              placeholder="What to do differently on this attempt"
              rows={3}
              value={note}
            />
          </label>
          {descriptor && (
            <label>
              <span>Model</span>
              <select className="input sm" value={model} disabled={busy} onChange={(event) => setModel(event.target.value)}>
                {models.map((choice) => (
                  <option key={choice.value} value={choice.value}>{choice.label}</option>
                ))}
              </select>
            </label>
          )}
          {descriptor && effortOption && (
            <label>
              <span>Effort</span>
              <select className="input sm" value={effectiveEffort ?? ""} disabled={busy} onChange={(event) => setEffort(event.target.value)}>
                {effortValues.map((value) => (
                  <option key={value} value={value}>{optionLabel(effortOption.field, value)}</option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}
    </>
  );
}

function FailureView({
  busy,
  data,
  events,
  onChangeLoop,
  onEditLoop,
  onRerun,
  onRetry,
  onSendToImplement,
  onTranscript,
  retrySeed,
  stage,
}: {
  busy: boolean;
  data: RunSurfaceData;
  events: AgentEvent[];
  onChangeLoop?: () => Promise<void>;
  onEditLoop?: () => void;
  onRerun?: () => Promise<void>;
  onRetry?: (override?: RetryStageOverride) => Promise<void>;
  onSendToImplement?: () => Promise<void>;
  onTranscript: () => void;
  retrySeed?: RetrySeed;
  stage: PlanLoopStageRun | null;
}) {
  const output = lastAgentOutput(events, data.statusReason);
  const cancelled = data.status === "cancelled";
  const retriesExhausted = Boolean(
    stage && stage.max_attempts > 0 && stage.attempt >= stage.max_attempts,
  );
  return (
    <>
      <div className="run-state-card failed">
        <AlertIcon size={15} />
        <div>
          <strong>
            {cancelled ? "Run cancelled" : `Run failed at ${stage?.name ?? "the current stage"}`}
            {!cancelled && (retriesExhausted
              ? ` · ${stage?.attempt ?? 1} automatic attempts exhausted`
              : " · the stage stopped before completing")}
          </strong>
          <p>{cancelled ? "The run was stopped." : humanFailure(data.statusReason)} The shared worktree is kept exactly as the stage left it.</p>
          {data.statusReason && <code>{data.statusReason}</code>}
          <div className="run-failure-options">
            {!cancelled && onRetry && (
              retrySeed && stage && (stage.kind === "agent_task" || stage.kind === "agent_review" || stage.kind === "pr") ? (
                <RetryStageControls
                  seed={retrySeed}
                  busy={busy}
                  onRetry={onRetry}
                  label={stage.kind === "pr" ? "Retry Create PR" : "Retry stage"}
                  hint={stage.kind === "pr" ? "same worktree · reuses your PR setup · picks up manual fixes" : "one more attempt · resumes the kept workspace"}
                />
              ) : (
                <><button className="btn primary sm" disabled={busy} onClick={() => void onRetry()}><LoopIcon size={11} /> {stage?.kind === "pr" ? "Retry Create PR" : "Retry stage"}</button><span>{stage?.kind === "pr" ? "same worktree · reuses your PR setup · picks up manual fixes" : "one more attempt · resumes the kept workspace"}</span></>
              )
            )}
            {!cancelled && stage?.kind === "pr" && onSendToImplement && <><button className="btn sm" disabled={busy} onClick={() => void onSendToImplement()}><ReturnIcon size={11} /> Send to Implement</button><span>runs the loop again · saved PR setup is reused</span></>}
            {onRerun && <><button className="btn sm" disabled={busy} onClick={() => void onRerun()}><ReturnIcon size={11} /> Start new run</button><span>latest saved loop · review setup first</span></>}
            {onChangeLoop && <><button className="btn sm" disabled={busy} onClick={() => void onChangeLoop()}><LoopIcon size={11} /> Change loop → new run</button><span>choose another loop · retained workspace</span></>}
            {onEditLoop && <><button className="btn sm" onClick={onEditLoop}><DocIcon size={11} /> Edit loop → new run</button><span>changes apply only to the next run</span></>}
          </div>
        </div>
      </div>
      <section className="run-last-output">
        <header><strong>Last agent output</strong><button type="button" onClick={onTranscript}>open full transcript ▸</button></header>
        <div>{output.map((line, index) => <span className={line.error ? "error" : undefined} key={`${line.text}-${index}`}>{line.text}</span>)}{output.length === 0 && <span className="dim">No agent output was recorded.</span>}</div>
      </section>
    </>
  );
}

function ResultView({
  busy,
  data,
  stage,
  onCreatePr,
}: {
  busy: boolean;
  data: RunSurfaceData;
  stage: RunStageOccurrence | null;
  onCreatePr?: () => void;
}) {
  return (
    <div className="run-result">
      <div className="run-result-summary doc">
        <CheckIcon size={16} />
        <div>
          <strong>{data.accepted ? "Result approved" : "Result ready for approval"}</strong>
          {/* The report below opens with the same summary, sectioned and
              whitespace-preserving. Repeat it here only when there is none. */}
          {!stage && <p>{data.summary || "The loop completed without a summary."}</p>}
        </div>
        {onCreatePr && <button className="btn primary" disabled={busy} onClick={onCreatePr}><span aria-hidden>⇱</span> Create PR</button>}
      </div>
      {stage ? <StageReport stage={stage} feedback={data.feedback} /> : (
        <>
          <section><header><strong>Changed files</strong><span>{data.changedFiles.length}</span></header>{data.changedFiles.map((file) => <div className="file-row" key={file.path}><span className="fname">{file.path}</span><span className="fstat"><span className="add">+{file.additions}</span><span className="del">-{file.deletions}</span></span></div>)}{data.changedFiles.length === 0 && <p className="dim">No changed files reported.</p>}</section>
          <section><header><strong>Validation evidence</strong></header>{data.evidence.map((item) => <span className="run-evidence" key={item}><CheckIcon size={9} /> {item}</span>)}{data.evidence.length === 0 && <p className="dim">No validation evidence reported.</p>}</section>
        </>
      )}
    </div>
  );
}

function FollowUpChooser({
  busy,
  canVerify,
  onStart,
}: {
  busy: boolean;
  canVerify: boolean;
  onStart: (kind: Exclude<LoopRunKind, "initial">, note: string) => Promise<void>;
}) {
  const [kind, setKind] = useState<Exclude<LoopRunKind, "initial">>("amend");
  const [note, setNote] = useState("");
  return (
    <section className="run-follow-up">
      <header><strong>Follow-up run</strong><span>seeds from the current branch and includes manual edits</span></header>
      <label className={kind === "amend" ? "selected" : ""}>
        <input type="radio" name="follow-up-kind" checked={kind === "amend"} onChange={() => setKind("amend")} />
        <ReturnIcon size={12} />
        <span><strong>Apply feedback</strong><small>enters at the task stage · runs the full remaining loop</small></span>
        {kind === "amend" && <textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Paste review comments or describe the changes to apply" />}
      </label>
      <label className={`${kind === "verify" ? "selected" : ""}${canVerify ? "" : " disabled"}`}>
        <input type="radio" name="follow-up-kind" checked={kind === "verify"} disabled={!canVerify} onChange={() => setKind("verify")} />
        <FlaskIcon size={12} />
        <span><strong>Verify current state</strong><small>{canVerify ? "starts at the first check · agent task stages are skipped" : "this loop has no review or check stage"}</small></span>
      </label>
      <footer>
        <span>Keeps the same branch; an open pull request is updated in place.</span>
        <button className="btn primary sm" disabled={busy || (kind === "amend" && !note.trim())} onClick={() => void onStart(kind, note.trim())}><ReturnIcon size={11} /> Start follow-up run</button>
      </footer>
    </section>
  );
}

const FEEDBACK_SOURCE_LABELS: Record<string, string> = {
  approval: "you, at approval",
  review: "the review stage",
  pr_comment: "a PR comment",
  pr_general: "PR feedback",
  retry: "you, on retry",
};

/** Collapse a request down to one readable line.
 *
 *  A PR comment arrives as raw GitHub Markdown: a `<!-- pr-commenter -->`
 *  provenance marker, headings, and link targets that can run to hundreds of
 *  characters of query string. None of it identifies the request at a glance,
 *  so it is stripped here and the full text stays on the row's `title`.
 */
function feedbackSummary(record: LoopFeedbackRecord): string {
  const fromItems = record.items
    .map((item) => {
      const body = cleanFeedbackText(stringValue(item.body));
      const where = stringValue(item.location);
      const who = stringValue(item.author);
      const prefix = [who, where].filter(Boolean).join(" at ");
      return prefix && body ? `${prefix}: ${body}` : body || prefix;
    })
    .filter(Boolean);
  return cleanFeedbackText(record.note) || fromItems.join(" · ");
}

function cleanFeedbackText(value: string): string {
  return value
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(^|\s)#{1,6}\s+/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

/** The requests one implementation pass was working from.
 *
 *  Shown on the implementation stage and nowhere else: this is the pairing that
 *  matters -- what was asked for, immediately above what the pass reported
 *  doing about it. Every other stage would be repeating run-level state under a
 *  heading it does not own; the user can come back here if they want it.
 */
function requestsForPass(
  feedback: LoopFeedbackRecord[],
  stage: RunStageOccurrence,
): LoopFeedbackRecord[] {
  return feedback.filter((record) => {
    // Opened against this pass or an earlier one that nothing has settled yet.
    if (record.pass_number > stage.passNumber) return false;
    // An attempt-scoped note (a retry instruction) travelled with one attempt
    // only, so it belongs to the pass that carried it and no later one.
    if (record.scope === "attempt" && record.pass_number !== stage.passNumber) return false;
    // A request answered before this occurrence reported was already settled
    // when the pass began, so it is not something this pass was asked to do.
    if (record.state === "answered" && record.answered_at && stage.recordedAt) {
      return record.answered_at >= stage.recordedAt;
    }
    return true;
  });
}

function RequestedChanges({
  feedback,
  stage,
}: {
  feedback: LoopFeedbackRecord[];
  stage: RunStageOccurrence;
}) {
  const [open, setOpen] = useState(false);
  const records = requestsForPass(feedback, stage);
  if (records.length === 0) return null;
  return (
    <div className="report-sec run-requested-changes">
      <button
        type="button"
        className="report-lbl"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <ReturnIcon size={11} />
        Requested changes
        <span>{records.length}</span>
        <em>{open ? "hide" : "show"}</em>
      </button>
      {open && (
        <ul>
          {[...records].reverse().map((record) => (
            <RunFeedbackRow key={record.id} record={record} />
          ))}
        </ul>
      )}
    </div>
  );
}

function RunFeedbackRow({ record }: { record: LoopFeedbackRecord }) {
  const answered = record.state === "answered";
  const source = FEEDBACK_SOURCE_LABELS[record.source] ?? record.source;
  const summary = feedbackSummary(record);
  return (
    <li className={answered ? "answered" : ""}>
      <span className={`tag ${answered ? "" : "warn"}`}>{answered ? "answered" : "open"}</span>
      <span className="body" title={record.note || undefined}>{summary || "(no text)"}</span>
      <em>
        pass {record.pass_number} · from {source}
        {answered && record.answered_by ? ` · answered by ${record.answered_by}` : ""}
      </em>
    </li>
  );
}

/** Remind the user what they already let stand before they approve again. */
function DismissedFindings({ data }: { data: RunSurfaceData }) {
  const pending = ["completed", "awaiting_approval"].includes(data.status) && !data.accepted;
  if (!pending || data.waivedFindings.length === 0) return null;
  return (
    <section className="run-dismissed-findings">
      <header>
        <strong>Already dismissed on this run</strong>
        <span className="tag">{data.waivedFindings.length}</span>
        <small>reviewers are told not to raise these again</small>
      </header>
      <ul>
        {data.waivedFindings.map((finding, index) => (
          <li key={`${finding}-${index}`}>{finding}</li>
        ))}
      </ul>
    </section>
  );
}

function RunActions({
  agentStage,
  busy,
  data,
  discussionAgent,
  discussionBusy,
  discussionStage,
  onApprove,
  onCancel,
  onStopStage,
  onChangeLoop,
  onDiscuss,
  onEditLoop,
  onOpenEditor,
  onOpenConsole,
  onRequestChanges,
  onTranscript,
}: {
  agentStage: PlanLoopStageRun | null;
  busy: boolean;
  data: RunSurfaceData;
  discussionAgent: AgentSummary | null;
  discussionBusy: boolean;
  discussionStage: PlanLoopStageRun | null;
  onApprove?: () => Promise<void>;
  onCancel?: () => Promise<void>;
  onStopStage?: () => Promise<void>;
  onChangeLoop?: () => Promise<void>;
  onDiscuss: (stage: PlanLoopStageRun, agent: AgentSummary) => void;
  onEditLoop?: () => void;
  onOpenEditor?: () => void;
  onOpenConsole?: () => void;
  onRequestChanges?: () => void;
  onTranscript: () => void;
}) {
  const active = ["pending", "running", "waiting_report", "assessing", "needs_agent", "blocked_user"].includes(data.status);
  const resultReady = ["completed", "awaiting_approval", "accepted", "cleaned"].includes(data.status);
  // A mid-pipeline approval stage hands off to a later stage instead of finishing the run.
  const approvalPass = data.definition?.stages
    .find((item) => item.id === data.currentStageId && item.kind === "user_approval")
    ?.transitions.pass;
  const approvalContinues = Boolean(approvalPass) && approvalPass !== "complete";
  return (
    <footer className="run-surface-actions">
      {agentStage?.agent_slug && <button className="btn" onClick={onTranscript}><DocIcon size={12} /> Open transcript</button>}
      {onOpenEditor && <button className="btn" onClick={onOpenEditor}><EditIcon size={12} /> Open in editor</button>}
      {onOpenConsole && <button className="btn" onClick={onOpenConsole}><span aria-hidden>&gt;_</span> Open console</button>}
      {discussionStage && <button className="btn chat" disabled={!discussionAgent || discussionBusy} onClick={() => discussionAgent && onDiscuss(discussionStage, discussionAgent)}><ChatIcon size={12} /> {discussionBusy ? "Opening chat…" : "Discuss in chat"}</button>}
      <span />
      {active && onStopStage && <button className="btn ghost" disabled={busy} onClick={() => window.confirm("Stop the running stage? The run keeps its workspace and you can retry the stage afterwards.") && void onStopStage()}>Stop stage</button>}
      {active && onCancel && <button className="btn danger ghost" disabled={busy} onClick={() => window.confirm("Cancel this run? Its shared workspace will remain available.") && void onCancel()}>Cancel run</button>}
      {resultReady && !data.accepted && onRequestChanges && <button className="btn" disabled={busy} onClick={onRequestChanges}><ReturnIcon size={12} /> Request changes</button>}
      {resultReady && !data.accepted && onApprove && <button className="btn approve" disabled={busy} onClick={() => void onApprove()}><CheckIcon size={12} /> {approvalContinues ? "Approve · continue" : "Approve result"}</button>}
      {data.accepted && onEditLoop && <button className="btn ghost" onClick={onEditLoop}>Edit loop → future runs</button>}
      {data.accepted && onChangeLoop && <button className="btn ghost" disabled={busy} onClick={() => void onChangeLoop()}>Change loop → new run</button>}
    </footer>
  );
}

function StageReport({ stage, feedback }: { stage: RunStageOccurrence; feedback: LoopFeedbackRecord[] }) {
  const findings = stage.finding_details.length > 0
    ? stage.finding_details
    : stage.findings.map((text) => ({ text, severity: "medium" as const, location: "" }));
  const selectedFindings = stage.reviewDecision?.enforced_findings.flatMap((index) => (
    findings[index] ? [findings[index]] : []
  )) ?? [];
  return (
    <div className="report doc">
      {stage.kind === "agent_task" && <RequestedChanges feedback={feedback} stage={stage} />}
      <ReportSection label={stage.kind === "agent_review" ? "Verdict & summary" : "Summary"} icon={<DocIcon size={11} />}><div className="report-summary">{stage.summary}</div></ReportSection>
      {stage.criteria_coverage.length > 0 && <ReportSection label="Acceptance criteria" icon={<CheckIcon size={11} />} count={stage.criteria_coverage.length}>{stage.criteria_coverage.map((criterion, index) => <div className={`crit ${criterion.met ? "met" : "unmet"}`} key={`${criterion.text}-${index}`}><span className="cbox" role="img" aria-label={criterion.met ? "Met" : "Not met"}>{criterion.met ? <CheckIcon size={12} /> : <span aria-hidden>×</span>}</span><span><span className="ctext">{criterion.text}</span>{criterion.note && <span className="cnote">{criterion.note}</span>}</span></div>)}</ReportSection>}
      {stage.findings.length > 0 && <ReportSection label="Findings" icon={<EyeIcon size={11} />} count={stage.findings.length}>{findings.map((finding, index) => <div className="finding" key={`${finding.text}-${index}`}><span className={`sev ${finding.severity}`}>{finding.severity}</span><span className="fbody"><span className="ftext">{finding.text}</span>{finding.location && <span className="floc">{finding.location}</span>}</span></div>)}</ReportSection>}
      {stage.reviewDecision && <ReportSection label="Human decision" icon={<UserCheckIcon size={11} />} count={selectedFindings.length}>
        <div className="report-summary">{stage.reviewDecision.decision === "approve_as_is" ? "Approved as is. No findings were sent back." : `Sent back to implementation with ${selectedFindings.length} of ${findings.length} findings selected.`}</div>
        {selectedFindings.map((finding, index) => <div className="note-line" key={`${finding.text}-${index}`}><CheckIcon size={10} /><span>{finding.text}</span></div>)}
        {stage.reviewDecision.instruction && <div className="note-line"><UserCheckIcon size={10} /><span><strong>User instruction:</strong> {stage.reviewDecision.instruction}</span></div>}
      </ReportSection>}
      {stage.changed_files.length > 0 && <ReportSection label="Changed files" icon={<BranchIcon size={11} />} count={stage.changed_files.length}>{stage.changed_files.map((file) => <div className="file-row" key={file.path}><span className="fname">{file.path}</span><span className="fstat"><span className="add">+{file.additions}</span><span className="del">-{file.deletions}</span></span></div>)}</ReportSection>}
      {meaningful(stage.validation_evidence) && <ReportSection label="Validation evidence" icon={<FlaskIcon size={11} />}><span className="run-evidence"><CheckIcon size={9} /> {stage.validation_evidence}</span></ReportSection>}
      {(meaningful(stage.divergences) || meaningful(stage.skipped_scope)) && <ReportSection label="Divergences & skipped scope" icon={<BranchIcon size={11} />}>{meaningful(stage.divergences) && <div className="note-line"><span>·</span><span>{stage.divergences}</span></div>}{meaningful(stage.skipped_scope) && <div className="note-line"><span>·</span><span>{stage.skipped_scope}</span></div>}</ReportSection>}
    </div>
  );
}

function ReportSection({ label, icon, count, children }: { label: string; icon: ReactNode; count?: number; children: ReactNode }) {
  return <div className="report-sec"><div className="report-lbl">{icon}{label}{count !== undefined && <span>{count}</span>}</div>{children}</div>;
}

export function buildRunDiscussionSeed(
  data: RunSurfaceData,
  stage: PlanLoopStageRun,
): string {
  const passNumber = "passNumber" in stage && typeof stage.passNumber === "number"
    ? stage.passNumber
    : null;
  const lines = [
    "Selected loop stage context (reference only)",
    `Run: ${data.id}`,
    `Stage: ${stage.name} (${stage.id})${passNumber ? ` · pass ${passNumber}` : ""}`,
  ];
  if (meaningful(stage.summary)) lines.push("", "Summary:", stage.summary);
  if (meaningful(stage.changes)) lines.push("", "Changes:", stage.changes);
  if (stage.artifact_refs.length > 0) {
    lines.push("", "Generated artifacts:", ...stage.artifact_refs.map((ref) => `- ${ref}`));
  }
  if (stage.changed_files.length > 0) {
    lines.push("", "Changed files:", ...stage.changed_files.map((file) => `- ${file.path}`));
  }
  const findings = stage.finding_details.length > 0
    ? stage.finding_details.map((finding) => `${finding.text}${finding.location ? ` (${finding.location})` : ""}`)
    : stage.findings;
  if (findings.length > 0) lines.push("", "Findings:", ...findings.map((finding) => `- ${finding}`));
  if (meaningful(stage.validation_evidence)) {
    lines.push("", "Validation evidence:", stage.validation_evidence);
  }
  return lines.join("\n");
}

function stringOptions(options: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(options).filter(
      (entry): entry is [string, string] => typeof entry[1] === "string",
    ),
  );
}

export function planningRunData(artifact: PlanArtifact, run: PlanArtifactRun): RunSurfaceData {
  const status = run.loop_status ?? (run.status === "accepted" ? "accepted" : run.status === "running" ? "running" : "awaiting_approval");
  const changedFiles = new Map<string, { path: string; additions: number; deletions: number }>();
  for (const stage of run.loop_stages) for (const file of stage.changed_files) changedFiles.set(file.path, file);
  const evidence = run.loop_stages.map((stage) => stage.validation_evidence.trim()).filter((value, index, values) => value && values.indexOf(value) === index);
  const number = Number.parseInt(run.id.match(/(\d+)$/)?.[1] ?? "1", 10);
  return {
    accepted: status === "accepted" || run.status === "accepted",
    changedFiles: [...changedFiles.values()],
    cleanupAt: run.cleanup_at,
    completedAt: run.completed_at,
    costUsd: null,
    currentStageId: run.loop_current_stage_id || null,
    definition: run.loop_definition ?? null,
    elapsedSeconds: elapsedBetween(run.started_at, run.completed_at),
    evidence,
    goal: artifact.title,
    id: run.id,
    loopName: run.loop_definition_name || run.loop_definition_id || "Loop",
    number: Number.isFinite(number) ? number : 1,
    passNumber: run.loop_pass_number ?? 1,
    passes: run.loop_passes ?? [],
    pr: run.pr ?? null,
    prComments: run.pr_comments ?? [],
    revision: run.loop_definition_revision,
    stages: run.loop_stages,
    startedAt: run.started_at,
    status,
    statusReason: run.loop_status_reason,
    summary: run.summary || [...run.loop_stages].reverse().find((stage) => stage.summary)?.summary || artifact.title,
    targetId: artifact.id,
    workspacePath: "",
    brief: planningRunBrief(artifact, run),
    runKind: "initial",
    seedLabel: "",
    reviewGate: run.loop_review_gate ?? null,
    feedback: run.feedback ?? [],
    waivedFindingsCount: run.waived_findings_count ?? 0,
    waivedFindings: run.waived_findings ?? [],
  };
}

function runGateLabel(data: RunSurfaceData): string | null {
  if (data.reviewGate) {
    return data.reviewGate.passes_spent
      ? `⚉ human check · ${data.reviewGate.passes_used}/${data.reviewGate.max_passes} spent`
      : data.reviewGate.mode === "human_check" ? "⚉ human check" : `⟲ auto · max ${data.reviewGate.max_passes}`;
  }
  const stage = data.definition?.stages.find((item) => item.review_gate && item.transitions.changes_requested);
  if (!stage?.review_gate) return null;
  const override = data.brief?.stages.find((item) => item.stage_id === stage.id)?.review_gate;
  const mode = stage.review_gate.locked ? stage.review_gate.mode : override ?? stage.review_gate.mode;
  return mode === "human_check" ? "⚉ human check" : `⟲ auto · max ${stage.review_gate.max_passes}`;
}

function planningRunBrief(artifact: PlanArtifact, run: PlanArtifactRun): LoopBrief | null {
  const note = run.brief_note?.trim();
  if (!note) return null;
  const stages = (run.loop_definition?.stages ?? [])
    .filter((stage) => stage.agent !== null)
    .map((stage) => ({ stage_id: stage.id, note, context: [], agent: null }));
  return stages.length > 0 ? { goal: artifact.title, stages } : null;
}

function outputStage(data: RunSurfaceData): PlanLoopStageRun | null {
  return data.stages.find((stage) => stage.id === data.currentStageId)
    ?? [...data.stages].reverse().find((stage) => stage.status !== "pending")
    ?? null;
}

function stageOccurrences(data: RunSurfaceData): RunStageOccurrence[] {
  const reports = data.stages.flatMap((stage) => (stage.reports ?? []).map((report, index) => ({
    ...stage,
    agent_slug: report.agent_slug ?? stage.agent_slug,
    artifact_refs: report.artifact_refs,
    blocker: report.blocker,
    changed_files: report.changed_files,
    changes: report.changes,
    context_warnings: stage.context_warnings,
    criteria_coverage: report.criteria_coverage,
    divergences: report.divergences,
    finding_details: report.finding_details,
    findings: report.findings,
    reviewDecision: report.review_decision ?? null,
    occurrenceId: `${stage.id}:${report.pass_number || 1}:${report.seq || index + 1}`,
    passNumber: report.pass_number || index + 1,
    push_at: report.push_at ?? stage.push_at,
    addressed_comments: report.addressed_comments ?? stage.addressed_comments,
    feedback_instruction: report.feedback_instruction ?? stage.feedback_instruction,
    recordedAt: report.recorded_at,
    reports: [],
    skipped_scope: report.skipped_scope,
    sourceStageId: stage.id,
    status: reportStatus(report),
    summary: report.summary,
    transcriptEndSeq: report.seq || null,
    validation_evidence: report.validation_evidence,
  } satisfies RunStageOccurrence)))
    .sort((left, right) => left.recordedAt.localeCompare(right.recordedAt));

  if (reports.length === 0) {
    return data.stages.map((stage) => ({
      ...stage,
      occurrenceId: `${stage.id}:1`,
      passNumber: 1,
      recordedAt: "",
      sourceStageId: stage.id,
      transcriptEndSeq: null,
    }));
  }

  const current = data.stages.find((stage) => stage.id === data.currentStageId) ?? null;
  if (!current) return reports;
  const latest = (current.reports ?? []).at(-1);
  const alreadyRecorded = latest && reportStatus(latest) === current.status;
  if (alreadyRecorded) return reports;
  const passNumber = data.passNumber;
  return [...reports, {
    ...current,
    occurrenceId: `${current.id}:${passNumber}`,
    passNumber,
    recordedAt: "",
    sourceStageId: current.id,
    transcriptEndSeq: null,
  }];
}

function transcriptDock(
  selected: RunStageOccurrence,
  occurrences: RunStageOccurrence[],
): Extract<RunDock, { kind: "transcript" }> {
  return {
    kind: "transcript",
    agentSlug: selected.agent_slug!,
    startSeq: transcriptStartSeq(selected, occurrences),
    endSeq: selected.transcriptEndSeq,
    title: `${selected.name} · pass ${selected.passNumber} transcript`,
  };
}

function occurrenceElapsed(
  selected: RunStageOccurrence,
  occurrences: RunStageOccurrence[],
  events: AgentEvent[],
): number | null {
  const startSeq = transcriptStartSeq(selected, occurrences);
  const prompt = events.find((event) => event.seq > startSeq && event.type === "user_input");
  if (!prompt) return null;
  return elapsedBetween(prompt.ts, selected.recordedAt || null);
}

function transcriptStartSeq(
  selected: RunStageOccurrence,
  occurrences: RunStageOccurrence[],
): number {
  return occurrences
    .slice(0, occurrences.findIndex((item) => item.occurrenceId === selected.occurrenceId))
    .filter((item) => item.agent_slug === selected.agent_slug)
    .reduce((latest, item) => Math.max(latest, item.transcriptEndSeq ?? 0), 0);
}

function passSummary(
  passes: Array<Record<string, unknown>> | undefined,
  passNumber: number,
): Record<string, unknown> | undefined {
  return passes?.find((item) => numberValue(item.number) === passNumber);
}

function passAddressedCount(
  summary: Record<string, unknown> | undefined,
  stages: RunStageOccurrence[],
): number {
  if (Array.isArray(summary?.addressed_comments)) return summary.addressed_comments.length;
  return stages.reduce((count, stage) => count + (stage.addressed_comments?.length ?? 0), 0);
}

function passElapsed(
  summary: Record<string, unknown> | undefined,
  stages: RunStageOccurrence[],
  runStartedAt: string | undefined,
): number | null {
  const persisted = numberValue(summary?.elapsed_seconds);
  if (persisted !== null) return persisted;
  const recorded = stages.map((stage) => stage.recordedAt).filter(Boolean);
  const startedAt = stringValue(summary?.started_at)
    || recorded[0]
    || (stages[0]?.passNumber === 1 ? runStartedAt ?? "" : "");
  const completedAt = stringValue(summary?.sealed_at) || recorded.at(-1) || "";
  return startedAt && completedAt ? elapsedBetween(startedAt, completedAt) : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function defaultOutputOccurrence(
  occurrences: RunStageOccurrence[],
  currentStageId: string | null,
): RunStageOccurrence | null {
  let currentIndex = -1;
  for (let index = occurrences.length - 1; index >= 0; index -= 1) {
    if (occurrences[index].sourceStageId === currentStageId) {
      currentIndex = index;
      break;
    }
  }
  const current = currentIndex >= 0 ? occurrences[currentIndex] : occurrences.at(-1) ?? null;
  const previous = currentIndex > 0 ? occurrences[currentIndex - 1] : null;
  if (current?.status === "running" && current.passNumber > 1 && previous?.status === "changes_requested") {
    return previous;
  }
  return current;
}

function outcomeStatus(outcome: NonNullable<PlanLoopStageRun["reports"]>[number]["outcome"]): PlanLoopStageRun["status"] {
  if (outcome === "pass") return "passed";
  if (outcome === "failed") return "failed";
  return outcome;
}

function reportStatus(
  report: NonNullable<PlanLoopStageRun["reports"]>[number],
): PlanLoopStageRun["status"] {
  return report.review_decision?.decision === "approve_as_is"
    ? "passed"
    : outcomeStatus(report.outcome);
}

function stageOrder(stages: RunStageOccurrence[], stageId: string): number {
  return stages.findIndex((stage) => stage.sourceStageId === stageId);
}

function stageIcon(stage: Pick<PlanLoopStageRun, "id" | "kind">, size: number) {
  if (stage.kind === "pr") return <BranchIcon size={size} />;
  if (stage.kind === "agent_review") return stage.id.includes("security") ? <ShieldIcon size={size} /> : <EyeIcon size={size} />;
  if (stage.kind === "user_approval") return <UserCheckIcon size={size} />;
  if (stage.kind === "deterministic_check") return <FlaskIcon size={size} />;
  return <SparkIcon size={size} />;
}

/* Merged carries the Atelier accent so a landed PR reads at a glance; open is
   good-toned, closed is danger. (Diverges from the handoff's neutral merged
   tag — a deliberate call: neutral was too subtle to spot in a rail.) */
export function prStatusTone(status: string): string {
  if (status === "merged") return "merged";
  if (status === "open") return "good";
  if (status === "closed") return "danger";
  return "";
}

function stageTone(status: PlanLoopStageRun["status"]): string {
  if (status === "blocked_user") return "blocked";
  if (status === "failed" || status === "cancelled") return "failed";
  if (status === "changes_requested") return "changes";
  return status;
}

function stageStatusLabel(status: PlanLoopStageRun["status"]): string {
  if (status === "blocked_user") return "blocked · user";
  if (status === "changes_requested") return "changes requested";
  return status;
}

function kindLabel(kind: PlanLoopStageRun["kind"]): string {
  if (kind === "pr") return "create pr";
  if (kind === "agent_task") return "agent task";
  if (kind === "agent_review") return "agent review";
  if (kind === "deterministic_check") return "check";
  return "approval";
}

function statusLabel(status: LoopStatus): string {
  if (status === "blocked_user") return "blocked · user";
  if (status === "awaiting_approval" || status === "completed") return "awaiting approval";
  if (status === "waiting_report") return "waiting report";
  if (status === "needs_agent") return "starting agent";
  return status.replaceAll("_", " ");
}

function runStatusTone(status: LoopStatus): string {
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "accepted" || status === "cleaned") return "good";
  if (status === "awaiting_approval" || status === "completed") return "info";
  return "warn";
}

function lastAgentOutput(
  events: AgentEvent[],
  statusReason: string,
): Array<{ text: string; error: boolean }> {
  let lastInput = -1;
  events.forEach((event, index) => {
    if (event.type === "user_input") lastInput = index;
  });
  const turn = events.slice(lastInput + 1);
  let completeIndex = -1;
  turn.forEach((event, index) => {
    if (event.type === "message_complete" && typeof event.text === "string") {
      completeIndex = index;
    }
  });
  const message = completeIndex >= 0 ? turn[completeIndex] : undefined;
  const partial = turn
    .slice(completeIndex + 1)
    .filter((event) => event.type === "message_delta" && typeof event.text === "string")
    .map((event) => event.text as string)
    .join("");
  const text = partial || (typeof message?.text === "string" ? message.text : "");
  const output = text.split("\n").filter(Boolean).slice(-3).map((line) => ({
    text: line,
    error: false,
  }));
  const error = [...turn]
    .reverse()
    .find((event) => event.type === "error" && typeof event.message === "string");
  const errorText = typeof error?.message === "string" ? error.message : statusReason.trim();
  if (errorText) output.push({ text: `Error: ${errorText}`, error: true });
  return output;
}

function humanFailure(reason: string): string {
  if (reason.toLowerCase().includes("report")) return "The stage ended without a valid step report, so the loop could not determine its outcome.";
  return reason || "The backend stopped this run after its automatic attempts were exhausted.";
}

function compactPath(path: string): string {
  const match = path.match(/^\/Users\/[^/]+\/(.*)$/);
  return match ? `~/${match[1]}` : path;
}

function formatElapsed(seconds: number | null): string {
  if (seconds === null) return "elapsed —";
  if (seconds < 60) return `${Math.round(seconds)} sec`;
  return `${Math.round(seconds / 60)} min`;
}

function elapsedBetween(startedAt: string, completedAt: string | null): number | null {
  const start = Date.parse(startedAt);
  const end = completedAt ? Date.parse(completedAt) : Date.now();
  return Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, (end - start) / 1_000) : null;
}

function meaningful(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return Boolean(normalized) && !["none", "none.", "n/a", "not applicable", "not applicable."].includes(normalized);
}
