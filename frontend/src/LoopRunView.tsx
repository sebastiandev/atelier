import { type ReactNode, useState } from "react";

import {
  type PlanArtifact,
  type PlanArtifactRun,
  type PlanLoopStageRun,
} from "./api";
import {
  AlertIcon,
  BranchIcon,
  CheckIcon,
  DocIcon,
  EyeIcon,
  FlaskIcon,
  LockIcon,
  LoopIcon,
  ReturnIcon,
  ShieldIcon,
  SparkIcon,
  UserCheckIcon,
} from "./Icons";

type LoopRunViewProps = {
  artifact: PlanArtifact;
  run: PlanArtifactRun;
  saving: boolean;
  onBack: () => void;
  onResolveBlocker: (agentSlug: string) => void;
  onRequestChanges: (note: string) => Promise<void>;
  onApprove: () => Promise<void>;
  onCleanup: () => void;
};

export function LoopRunView({
  artifact,
  run,
  saving,
  onBack,
  onResolveBlocker,
  onRequestChanges,
  onApprove,
  onCleanup,
}: LoopRunViewProps) {
  const awaiting = run.loop_status === "awaiting_approval";
  if (awaiting || run.loop_status === "accepted" || run.status === "accepted") {
    return (
      <LoopResultView
        artifact={artifact}
        run={run}
        saving={saving}
        onBack={onBack}
        onRequestChanges={onRequestChanges}
        onApprove={onApprove}
        onCleanup={onCleanup}
      />
    );
  }
  const current = run.loop_stages.find((stage) => stage.id === run.loop_current_stage_id);
  return (
    <div className="run">
      <RunHeader artifact={artifact} run={run} onBack={onBack} />
      <div className="run-body themed-scrollbar">
        <div className="run-wrap">
          {run.loop_status === "blocked_user" && (
            <div className="blocker">
              <span className="bk-ico"><AlertIcon size={15} /></span>
              <div className="bk-meta">
                <div className="bk-t">{current?.name ?? "Current stage"} needs a decision</div>
                <div className="bk-d">The run is paused and will not consume retries until you resolve the blocker.</div>
                <div className="bk-q">{current?.blocker || run.loop_status_reason}</div>
                <div className="bk-acts">
                  <button
                    className="btn primary sm"
                    disabled={saving || !current?.agent_slug}
                    onClick={() => current?.agent_slug && onResolveBlocker(current.agent_slug)}
                  >
                    <CheckIcon size={11} /> Mark resolved &amp; resume
                  </button>
                </div>
              </div>
            </div>
          )}
          {run.loop_stages.map((stage, index) => (
            <RunStage
              key={stage.id}
              stage={stage}
              last={index === run.loop_stages.length - 1}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function RunHeader({
  artifact,
  run,
  onBack,
}: {
  artifact: PlanArtifact;
  run: PlanArtifactRun;
  onBack: () => void;
}) {
  const statusClass = run.loop_status === "blocked_user" || run.loop_status === "failed"
    ? "blocked"
    : run.loop_status === "awaiting_approval" || run.loop_status === "accepted" || run.loop_status === "cleaned"
      ? "await"
      : "running";
  const label = run.loop_status === "blocked_user"
    ? "Blocked · user"
    : run.loop_status === "awaiting_approval"
      ? "Awaiting approval"
      : run.loop_status === "accepted"
        ? "Approved"
        : run.loop_status === "cleaned"
          ? "Cleaned"
        : run.loop_status === "failed"
          ? "Failed"
          : "Running";
  return (
    <div className="run-hd" data-arti={artifact.kind}>
      <button className="btn ghost icon sm" onClick={onBack} aria-label="Back to artifact">←</button>
      <div className="rh-meta">
        <div className="rh-t"><LoopIcon size={14} /> {artifact.title}</div>
        <div className="rh-d">
          <span>{artifact.id}</span><span>·</span>
          <span>{run.loop_definition_name || run.loop_definition_id} <i>rev {run.loop_definition_revision}</i></span>
          <span>·</span><span>{run.agent_slug}</span>
        </div>
      </div>
      <span className={`run-status-pill ${statusClass}`}><span className="dot" />{label}</span>
    </div>
  );
}

function RunStage({ stage, last }: { stage: PlanLoopStageRun; last: boolean }) {
  const state = stageStatus(stage.status);
  return (
    <div className="rstage" data-persona={stagePersona(stage)}>
      <div className="rstage-rail">
        <span className={`rstage-node ${state}`}>
          {state === "passed" ? <CheckIcon size={14} /> : state === "blocked" ? <AlertIcon size={13} /> : stageIcon(stage)}
        </span>
        {!last && <span className={`rstage-line${state === "passed" ? " done" : ""}`} />}
      </div>
      <div className="rstage-body">
        <div className="rstage-hd">
          <span className="rs-name">{stage.name}</span>
          <span className={`rs-state ${state}`}>{stageStatusLabel(stage.status)}</span>
          {stage.attempt > 0 && (
            <span className="rs-attempt"><LoopIcon size={10} /> attempt {stage.attempt}/{stage.max_attempts}</span>
          )}
        </div>
        {stage.status !== "pending" && stage.kind !== "user_approval" && (
          <div className="rstage-meta">
            {stage.permissions && <span className={`seg ${stage.permissions}`}>{stage.permissions === "write" ? "write" : "read-only"}</span>}
            {stage.session && <span className="seg">{stage.session} session</span>}
            {stage.agent_slug && <span className="seg">{stage.agent_slug}</span>}
          </div>
        )}
        {stage.context_warnings.length > 0 && (
          <div className="rstage-context-warning"><AlertIcon size={11} /> Optional context not found: {stage.context_warnings.join(", ")}</div>
        )}
        {stage.status === "running" && (
          <div className="report loop-live-report">
            <span className="matz-mini" />
            <span>{stage.kind === "agent_review" ? "Reviewing the workspace against acceptance criteria…" : "Working on the assigned artifact…"}</span>
          </div>
        )}
        {stage.summary && stage.status !== "running" && <StageReport stage={stage} />}
        {stage.status === "pending" && (
          <div className="rstage-pending">{stage.kind === "user_approval" ? "Waits for approval once automatic stages pass." : "Runs after the previous stage passes."}</div>
        )}
      </div>
    </div>
  );
}

function StageReport({ stage }: { stage: PlanLoopStageRun }) {
  return (
    <div className="report">
      <ReportSection label={stage.kind === "agent_review" ? "Verdict & summary" : "Summary"} icon={<DocIcon size={11} />}>
        <div className="report-summary">{stage.summary}</div>
      </ReportSection>
      {stage.criteria_coverage.length > 0 && (
        <ReportSection label="Acceptance criteria" icon={<CheckIcon size={11} />} count={stage.criteria_coverage.length}>
          {stage.criteria_coverage.map((criterion, index) => (
            <div className={`crit ${criterion.met ? "met" : "unmet"}`} key={`${criterion.text}-${index}`}>
              <span className="cbox">{criterion.met && <CheckIcon size={9} />}</span>
              <span><span className="ctext">{criterion.text}</span>{criterion.note && <span className="cnote">{criterion.note}</span>}</span>
            </div>
          ))}
        </ReportSection>
      )}
      {stage.findings.length > 0 && (
        <ReportSection label="Findings" icon={<EyeIcon size={11} />} count={stage.findings.length}>
          {(stage.finding_details.length > 0
            ? stage.finding_details
            : stage.findings.map((text) => ({ text, severity: "medium" as const, location: "" })))
            .map((finding, index) => (
              <div className="finding" key={`${finding.text}-${index}`}>
                <span className={`sev ${finding.severity}`}>{finding.severity}</span>
                <span className="fbody"><span className="ftext">{finding.text}</span>{finding.location && <span className="floc">{finding.location}</span>}</span>
              </div>
            ))}
        </ReportSection>
      )}
      {stage.changed_files.length > 0 && (
        <ReportSection label="Changed files" icon={<BranchIcon size={11} />} count={stage.changed_files.length}>
          {stage.changed_files.map((file) => <div className="file-row" key={file.path}><span className="fname">{file.path}</span><span className="fstat"><span className="add">+{file.additions}</span><span className="del">-{file.deletions}</span></span></div>)}
        </ReportSection>
      )}
      {meaningful(stage.changes) && (
        <ReportSection label="Changes" icon={<BranchIcon size={11} />}><div className="report-summary mono">{stage.changes}</div></ReportSection>
      )}
      {meaningful(stage.validation_evidence) && (
        <ReportSection label="Validation evidence" icon={<FlaskIcon size={11} />}><span className="ev-pill pass"><span className="dot" />{stage.validation_evidence}</span></ReportSection>
      )}
      {(meaningful(stage.divergences) || meaningful(stage.skipped_scope)) && (
        <ReportSection label="Divergences & skipped scope" icon={<BranchIcon size={11} />}>
          {meaningful(stage.divergences) && <div className="note-line"><span>⑂</span><span>{stage.divergences}</span></div>}
          {meaningful(stage.skipped_scope) && <div className="note-line"><span>–</span><span>{stage.skipped_scope}</span></div>}
        </ReportSection>
      )}
    </div>
  );
}

function ReportSection({ label, icon, count, children }: { label: string; icon: ReactNode; count?: number; children: ReactNode }) {
  return <div className="report-sec"><div className="report-lbl">{icon}{label}{count !== undefined && <span>{count}</span>}</div>{children}</div>;
}

function LoopResultView({
  artifact,
  run,
  saving,
  onBack,
  onRequestChanges,
  onApprove,
  onCleanup,
}: Omit<LoopRunViewProps, "onResolveBlocker">) {
  const [requesting, setRequesting] = useState(false);
  const [note, setNote] = useState("");
  const accepted = run.loop_status === "accepted" || run.status === "accepted";
  const implementation = [...run.loop_stages].reverse().find((stage) => stage.kind === "agent_task");
  const reviews = run.loop_stages.filter((stage) => stage.kind === "agent_review");
  return (
    <div className="run">
      <RunHeader artifact={artifact} run={run} onBack={onBack} />
      <div className="result-body themed-scrollbar">
        <div className="result-wrap">
          <div className="result-hero">
            <span className="rhi"><CheckIcon size={17} /></span>
            <div className="rh-meta"><div className="rh-t">{accepted ? "Result approved" : "Result ready for approval"}</div><div className="rh-d">{implementation?.summary || run.summary}</div></div>
          </div>
          {implementation && (implementation.changed_files.length > 0 || meaningful(implementation.changes)) && <ResultSection label="Changed files & implementation" icon={<BranchIcon size={11} />}>{implementation.changed_files.map((file) => <div className="file-row" key={file.path}><span className="fname">{file.path}</span><span className="fstat"><span className="add">+{file.additions}</span><span className="del">-{file.deletions}</span></span></div>)}{meaningful(implementation.changes) && <div className="report-summary mono">{implementation.changes}</div>}</ResultSection>}
          {implementation && meaningful(implementation.validation_evidence) && <ResultSection label="Validation evidence" icon={<FlaskIcon size={11} />}><span className="ev-pill pass"><span className="dot" />{implementation.validation_evidence}</span></ResultSection>}
          {reviews.map((review) => <ResultSection key={review.id} label={review.name} icon={review.id.includes("security") ? <ShieldIcon size={11} /> : <EyeIcon size={11} />}><div className="result-verdict"><span className="verdict pass"><CheckIcon size={10} /> pass</span><span>{review.summary}</span></div>{(review.finding_details.length > 0 ? review.finding_details : review.findings.map((text) => ({ text, severity: "resolved" as const, location: "" }))).map((finding, index) => <div className="finding" key={`${finding.text}-${index}`}><span className={`sev ${finding.severity}`}>{finding.severity}</span><span className="fbody"><span className="ftext">{finding.text}</span>{finding.location && <span className="floc">{finding.location}</span>}</span></div>)}</ResultSection>)}
          {(meaningful(run.divergences) || meaningful(run.skipped_scope)) && <ResultSection label="Divergences & skipped scope" icon={<BranchIcon size={11} />}>{meaningful(run.divergences) && <div className="note-line"><span>⑂</span><span>{run.divergences}</span></div>}{meaningful(run.skipped_scope) && <div className="note-line"><span>–</span><span>{run.skipped_scope}</span></div>}</ResultSection>}
          {artifact.proposals.length > 0 && <ResultSection label="Proposed source-plan changes" icon={<DocIcon size={11} />} count={artifact.proposals.length}>{artifact.proposals.map((proposal) => <div className="plan-change" key={proposal.id}><DocIcon size={12} /><span>{proposal.title}</span><span className="pc-target">{proposal.artifact_id}</span></div>)}</ResultSection>}
          {requesting && !accepted && (
            <div className="rc-box">
              <div className="rc-lbl">Request changes</div>
              <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="What needs to change? This note is sent to the implementation stage…" autoFocus />
              <div className="rc-row"><span className="rc-dest"><ReturnIcon size={12} /> → Implementation · session reused</span><span className="spacer" /><button className="btn sm" onClick={() => setRequesting(false)}>Cancel</button><button className="btn sm warn" disabled={saving || !note.trim()} onClick={() => void onRequestChanges(note.trim())}><ReturnIcon size={11} /> Send &amp; re-run</button></div>
            </div>
          )}
          {accepted && <div className="loop-result-note"><CheckIcon size={13} /><span>Durable summary written and eligible dependencies unlocked. Cleanup is now available.</span></div>}
        </div>
      </div>
      <div className="result-actions">
        <span className="ra-hint"><LockIcon size={12} /> {accepted ? "This result is approved." : "Approve is enabled only while awaiting final approval."}</span>
        {accepted ? <button className="btn" disabled={saving || run.cleanup_at !== null} onClick={onCleanup}><SparkIcon size={12} /> {run.cleanup_at ? "Cleaned up" : "Clean up"}</button> : <><button className="btn" disabled={saving} onClick={() => setRequesting((value) => !value)}><ReturnIcon size={12} /> Request changes</button><button className="btn approve" disabled={saving} onClick={() => void onApprove()}><CheckIcon size={12} /> Approve result</button></>}
      </div>
    </div>
  );
}

function ResultSection({ label, icon, count, children }: { label: string; icon: ReactNode; count?: number; children: ReactNode }) {
  return <section className="result-sec"><div className="result-sec-hd">{icon}{label}{count !== undefined && <span className="rs-count">{count}</span>}</div><div className="result-sec-bd">{children}</div></section>;
}

function stageIcon(stage: PlanLoopStageRun) {
  if (stage.kind === "agent_review") return stage.id.includes("security") ? <ShieldIcon size={14} /> : <EyeIcon size={14} />;
  if (stage.kind === "user_approval") return <UserCheckIcon size={14} />;
  if (stage.kind === "deterministic_check") return <FlaskIcon size={14} />;
  return <SparkIcon size={14} />;
}

function stagePersona(stage: PlanLoopStageRun) {
  if (stage.kind === "agent_review") return "ux";
  if (stage.kind === "deterministic_check") return "product";
  if (stage.kind === "user_approval") return "writer";
  return "developer";
}

function stageStatus(status: PlanLoopStageRun["status"]) {
  if (status === "blocked_user" || status === "failed" || status === "cancelled") return "blocked";
  if (status === "changes_requested") return "changes";
  return status;
}

function stageStatusLabel(status: PlanLoopStageRun["status"]) {
  if (status === "blocked_user") return "blocked · user";
  if (status === "changes_requested") return "changes requested";
  return status;
}

function meaningful(value: string) {
  const normalized = value.trim().toLowerCase();
  return Boolean(normalized) && !["none", "none.", "n/a", "not applicable", "not applicable."].includes(normalized);
}
