import { useEffect, useState } from "react";

import { type PrConfig, getGitBranchListing } from "./api";
import {
  modelPickerOptions,
  optionLabel,
  providerEffortOption,
  providerFastOption,
  useProviderDescriptors,
} from "./providerDescriptors";

type AgentConfig = {
  provider: string;
  model: string;
  options: Record<string, string>;
};

export function CreatePrDialog({
  goal,
  inheritedAgent,
  onClose,
  onCreate,
  runLabel = "run",
  workspacePath,
}: {
  goal: string;
  inheritedAgent: AgentConfig | null;
  onClose: () => void;
  onCreate: (setup: PrConfig) => Promise<void>;
  runLabel?: string;
  workspacePath: string;
}) {
  const [name, setName] = useState(goal.trim());
  const [descriptionMode, setDescriptionMode] = useState<PrConfig["description_mode"]>("automatic");
  const [descriptionInstructions, setDescriptionInstructions] = useState("");
  const [manualBody, setManualBody] = useState("");
  const [status, setStatus] = useState<PrConfig["status"]>("draft");
  const [branches, setBranches] = useState<string[]>([]);
  const [detached, setDetached] = useState(false);
  const [baseBranch, setBaseBranch] = useState("master");
  const [branchName, setBranchName] = useState("");
  const [editingExecution, setEditingExecution] = useState(false);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [fast, setFast] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { descriptors } = useProviderDescriptors();
  const inheritedEffort = inheritedAgent?.options.effort
    ?? inheritedAgent?.options.reasoning_effort
    ?? inheritedAgent?.options.thinking_effort
    ?? "";
  const effectiveProvider = provider || inheritedAgent?.provider || "";
  const providerDescriptor = descriptors?.find((item) => item.name === effectiveProvider) ?? null;
  const effectiveModel = model || inheritedAgent?.model || providerDescriptor?.primary_field.default || "";
  const modelOptions = providerDescriptor ? modelPickerOptions(providerDescriptor) : [];
  const effortOption = providerDescriptor
    ? providerEffortOption(providerDescriptor, effectiveModel)
    : null;
  const fastOption = providerDescriptor ? providerFastOption(providerDescriptor) : null;
  const effectiveEffort = effort || inheritedEffort;
  const effectiveFast = fast ?? (inheritedAgent?.options["fast-mode"] === "on");

  useEffect(() => {
    if (!workspacePath) return;
    let cancelled = false;
    getGitBranchListing(workspacePath)
      .then((listing) => {
        if (cancelled) return;
        const items = listing.branches;
        setBranches(items);
        setDetached(listing.detached);
        setBaseBranch((current) => {
          if (items.includes(current)) return current;
          return items.find((item) => item === "main" || item === "master") ?? items[0] ?? current;
        });
      })
      .catch(() => {
        setBranches([]);
        setDetached(false);
      });
    return () => { cancelled = true; };
  }, [workspacePath]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose]);

  async function submit() {
    if (!name.trim() || !baseBranch.trim() || (detached && !branchName.trim()) || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onCreate({
        name: name.trim(),
        description_mode: descriptionMode,
        description_instructions: descriptionInstructions.trim(),
        manual_body: manualBody.trim(),
        status,
        base_branch: baseBranch.trim(),
        branch_name: branchName.trim(),
        provider: provider.trim() || null,
        model: model.trim() || null,
        effort: effort.trim() || null,
        fast,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="scrim" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) onClose();
    }}>
      <div className="modal create-pr-modal" role="dialog" aria-modal="true" aria-labelledby="create-pr-title">
        <div className="modal-hd">
          <div className="create-pr-heading">
            <h3 id="create-pr-title"><span aria-hidden>⇱</span> Create PR</h3>
            <span className="sub">{runLabel} accepted · a fresh agent commits, pushes and opens the PR</span>
          </div>
          <button className="btn icon" aria-label="Close" disabled={busy} onClick={onClose}>×</button>
        </div>
        <div className="modal-bd create-pr-body">
          <label className="field create-pr-name">
            <span className="label"><strong>PR name</strong><em>derived from the goal · edit freely</em></span>
            <input autoFocus value={name} onChange={(event) => setName(event.target.value)} />
          </label>

          <fieldset className="create-pr-description">
            <legend>Description</legend>
            <div className={`create-pr-description-option${descriptionMode === "automatic" ? " selected" : ""}`}>
              <label>
                <input type="radio" name="pr-description" checked={descriptionMode === "automatic"} onChange={() => setDescriptionMode("automatic")} />
                <span><strong>Automatic</strong><small>the agent writes it from the run report</small></span>
              </label>
              {descriptionMode === "automatic" && <input value={descriptionInstructions} onChange={(event) => setDescriptionInstructions(event.target.value)} placeholder="optional instructions for the description…" />}
            </div>
            <div className={`create-pr-description-option${descriptionMode === "manual" ? " selected" : ""}`}>
              <label>
                <input type="radio" name="pr-description" checked={descriptionMode === "manual"} onChange={() => setDescriptionMode("manual")} />
                <span><strong>Manual</strong><small>write it here yourself · markdown</small></span>
              </label>
              {descriptionMode === "manual" && <textarea rows={5} value={manualBody} onChange={(event) => setManualBody(event.target.value)} placeholder="Pull request description" />}
            </div>
          </fieldset>

          <div className="create-pr-fields">
            <div className="field">
              <span className="label">Status</span>
              <span className="segmented">
                <button type="button" className={status === "draft" ? "active" : ""} aria-pressed={status === "draft"} onClick={() => setStatus("draft")}>draft</button>
                <button type="button" className={status === "open" ? "active" : ""} aria-pressed={status === "open"} onClick={() => setStatus("open")}>open</button>
              </span>
            </div>
            <label className="field">
              <span className="label">Base</span>
              {branches.length > 0 ? (
                <select aria-label="Base branch" value={baseBranch} onChange={(event) => setBaseBranch(event.target.value)}>
                  {branches.map((branch) => <option key={branch} value={branch}>{branch}</option>)}
                </select>
              ) : <input aria-label="Base branch" value={baseBranch} onChange={(event) => setBaseBranch(event.target.value)} />}
            </label>
          </div>

          {detached && <label className="field create-pr-branch detached">
            <span className="label"><span aria-hidden>⚠</span> Worktree is on a <strong>detached HEAD</strong> — name the branch to push</span>
            <input className="mono" value={branchName} onChange={(event) => setBranchName(event.target.value)} placeholder="feat/short-description" />
          </label>}

          <section className="create-pr-execution">
            <div className="create-pr-execution-summary">
              <span>execution · ⇡ inherits Implement — {effectiveProvider || "run provider"} · {effectiveModel || "run model"}{effectiveEffort && ` · effort ${effectiveEffort}`}{effectiveFast && " · fast"}</span>
              <button type="button" aria-expanded={editingExecution} onClick={() => setEditingExecution((value) => !value)}>✎ change</button>
            </div>
            {editingExecution && <div className="create-pr-execution-fields">
              <label><span>Provider</span><select value={provider} onChange={(event) => {
                const next = event.target.value;
                const descriptor = descriptors?.find((item) => item.name === next) ?? null;
                const nextModel = descriptor?.primary_field.default ?? "";
                setProvider(next);
                setModel(nextModel);
                setEffort(descriptor ? providerEffortOption(descriptor, nextModel)?.field.default ?? "" : "");
                setFast(next ? false : null);
              }}><option value="">inherit {inheritedAgent?.provider ? `(${inheritedAgent.provider})` : "from run"}</option>{descriptors?.map((item) => <option key={item.name} value={item.name}>{item.label}</option>)}</select></label>
              <label><span>Model</span><select value={model} disabled={!providerDescriptor} onChange={(event) => {
                const next = event.target.value;
                setModel(next);
                if (providerDescriptor) {
                  const nextEffort = providerEffortOption(providerDescriptor, next);
                  setEffort((current) => nextEffort?.field.values.includes(current) ? current : nextEffort?.field.default ?? "");
                }
              }}>{!provider && <option value="">inherit {inheritedAgent?.model ? `(${inheritedAgent.model})` : "from run"}</option>}{effectiveModel && !modelOptions.some((item) => item.value === effectiveModel) && <option value={effectiveModel}>{effectiveModel}</option>}{modelOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
              <label><span>Effort</span>{effortOption ? <select value={effort} onChange={(event) => setEffort(event.target.value)}>{!provider && <option value="">inherit {inheritedEffort ? `(${inheritedEffort})` : "from run"}</option>}{effortOption.field.values.map((value) => <option key={value} value={value}>{optionLabel(effortOption.field, value)}</option>)}</select> : <span className="dim">Not available</span>}</label>
              {fastOption && <div className="create-pr-fast-field"><span>Fast mode</span><label className="pm-fast-toggle"><input type="checkbox" aria-label="Fast mode" checked={effectiveFast} onChange={(event) => setFast(event.target.checked)} /><span aria-hidden />Fast</label></div>}
            </div>}
          </section>

          <p className="create-pr-note">shell &amp; tool calls may ask for approval while it runs — you approve them inline, like any stage</p>
          {error && <div className="form-error" role="alert">{error}</div>}
        </div>
        <div className="modal-ft">
          <span>one PR per work — later runs update the same branch</span>
          <span className="spacer" />
          <button className="btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !name.trim() || !baseBranch.trim() || (detached && !branchName.trim()) || (descriptionMode === "manual" && !manualBody.trim())} onClick={() => void submit()}><span aria-hidden>⇱</span> {busy ? "Starting…" : "Create PR"}</button>
        </div>
      </div>
    </div>
  );
}
