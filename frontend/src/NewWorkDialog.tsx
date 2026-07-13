import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import {
  type CreateWorkPayload,
  type LoopDefinition,
  type ProjectSummary,
  type WorkDetail,
  listLoopDefinitions,
} from "./api";
import { FolderPickerDialog } from "./FolderPickerDialog";
import {
  AgentIcon,
  CheckIcon,
  DocIcon,
  FolderIcon,
  LoopIcon,
  PersonIcon,
  SearchIcon,
} from "./Icons";
import { LoopDefinitionChoice } from "./LoopUI";
import { PlanningAgentControls } from "./PlanningMode";
import { type LoopStartSeed } from "./loopSetup";
import {
  PLANNING_FRAMEWORKS,
  PLANNING_PROFILES,
  type PlanningAgentConfig,
  type PlanningFrameworkId,
  type PlanningStartSeed,
  planningFrameworkDefinition,
  planningProfileDefinition,
} from "./planningSetup";

type WorkMode = "manual" | "planning" | "loop";

export type NewWorkIntent =
  | { mode: "manual" }
  | { mode: "planning"; seed: PlanningStartSeed }
  | { mode: "loop"; seed: LoopStartSeed };

type Props = {
  onClose: () => void;
  onCreate: (
    payload: CreateWorkPayload,
    intent: NewWorkIntent,
  ) => Promise<WorkDetail>;
  projects?: ProjectSummary[];
  // When opened from a project-scoped context, seed the picker. ``null``
  // is "Loose"; ``undefined`` leaves the picker free.
  presetProjectSlug?: string | null;
  // True when the project context is mandatory (e.g. opened from a
  // project detail screen). Disables the picker so the user can't
  // accidentally retarget the work elsewhere.
  lockProjectSlug?: boolean;
};

const MODE_CARDS: Array<{
  id: WorkMode;
  glyph: "manual" | "planning" | "loop";
  tag: string;
  title: string;
  desc: string;
}> = [
  {
    id: "manual",
    glyph: "manual",
    tag: "current",
    title: "Manual",
    desc: "Steer agents by hand",
  },
  {
    id: "planning",
    glyph: "planning",
    tag: "BMAD",
    title: "Planning",
    desc: "Use a planning framework",
  },
  {
    id: "loop",
    glyph: "loop",
    tag: "new",
    title: "Loop",
    desc: "Run to a verified goal",
  },
];

const NEW_WORK_TYPES = new Set(["feature", "bugfix", "refactor", "migration", "full_app"]);

export function NewWorkDialog({
  onClose,
  onCreate,
  projects = [],
  presetProjectSlug,
  lockProjectSlug = false,
}: Props) {
  const [title, setTitle] = useState("");
  const [idea, setIdea] = useState("");
  const [projectSlug, setProjectSlug] = useState<string | null>(
    presetProjectSlug ?? null,
  );
  const [mode, setMode] = useState<WorkMode>("planning");
  const [framework, setFramework] = useState<PlanningFrameworkId>("bmad");
  const [profile, setProfile] = useState("feature" as PlanningStartSeed["profile"]);
  const [folder, setFolder] = useState("");
  const [planDir, setPlanDir] = useState("");
  const [planDirDefault, setPlanDirDefault] = useState(true);
  const [agentConfig, setAgentConfig] = useState<PlanningAgentConfig | null>(null);
  const [loopDefinitions, setLoopDefinitions] = useState<LoopDefinition[] | null>(null);
  const [loopDefinitionId, setLoopDefinitionId] = useState("");
  const [loopQuery, setLoopQuery] = useState("");
  const [createLoop, setCreateLoop] = useState(false);
  const [loopError, setLoopError] = useState<string | null>(null);
  const [picker, setPicker] = useState<"work" | "plan" | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  const selectedProject = useMemo(
    () => projects.find((p) => p.slug === projectSlug) ?? null,
    [projects, projectSlug],
  );
  const selectedFramework = planningFrameworkDefinition(framework);
  const selectedProfile = planningProfileDefinition(profile);
  const hasTitle = title.trim().length > 0;
  const canSubmit =
    !submitting &&
    ((mode === "manual" && hasTitle) ||
      ((mode === "planning" || mode === "loop") &&
        hasTitle &&
        folder.trim().length > 0 &&
        (mode !== "loop" || createLoop || loopDefinitionId.length > 0)));

  useEffect(() => {
    titleRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const inheritedFolder = selectedProject?.default_folder?.trim() ?? "";
    setFolder(inheritedFolder);
    setPlanDir(inheritedFolder ? defaultPlanDir(inheritedFolder) : "");
    setPlanDirDefault(true);
  }, [selectedProject?.slug, selectedProject?.default_folder]);

  useEffect(() => {
    if (mode !== "loop" || loopDefinitions !== null || loopError) return;
    let cancelled = false;
    listLoopDefinitions(null, folder || null)
      .then((rows) => {
        if (cancelled) return;
        const available = rows.filter((row) => row.valid);
        setLoopDefinitions(available);
        setLoopDefinitionId(
          available.find((row) => row.is_default)?.id ?? available[0]?.id ?? "",
        );
      })
      .catch((reason) => {
        if (!cancelled) {
          setLoopError(reason instanceof Error ? reason.message : String(reason));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [loopDefinitions, loopError, mode]);

  function pickWorkFolder(path: string) {
    setFolder(path);
    setLoopDefinitions(null);
    setLoopError(null);
    if (planDirDefault || !planDir.trim()) {
      setPlanDir(defaultPlanDir(path));
      setPlanDirDefault(true);
    }
    setPicker(null);
  }

  function clearWorkFolder() {
    setFolder("");
    setLoopDefinitions(null);
    setLoopError(null);
    if (planDirDefault) setPlanDir("");
  }

  function pickPlanDir(path: string) {
    setPlanDir(path);
    setPlanDirDefault(path === defaultPlanDir(folder));
    setPicker(null);
  }

  async function submit() {
    if (!canSubmit) return;
    const payload: CreateWorkPayload = {
      name: title.trim(),
      description: idea.trim(),
      project_slug: projectSlug,
    };
    const intent: NewWorkIntent = mode === "planning"
      ? {
          mode: "planning",
          seed: {
            idea: idea.trim(),
            framework,
            profile,
            folder,
            planDir: planDir.trim() || null,
            agentConfig,
          },
        }
      : mode === "loop"
        ? {
            mode: "loop",
            seed: {
              goal: idea.trim(),
              folder,
              definitionId: loopDefinitionId || undefined,
              createDefinition: createLoop || undefined,
            },
          }
        : { mode: "manual" };
    setSubmitting(true);
    setError(null);
    try {
      await onCreate(payload, intent);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="scrim" onClick={onClose}>
      <div
        className={`modal nw-modal mode-${mode}`}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        style={
          selectedProject
            ? { ["--proj-h" as string]: String(selectedProject.color) }
            : undefined
        }
      >
        <div className="modal-hd">
          <div>
            <h3>New work</h3>
          </div>
          <button className="btn ghost icon sm" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="modal-bd">
          <div className="nw-identity">
            <label className="nw-field">
              <span className="nw-lbl">Title</span>
              <input
                ref={titleRef}
                className="nw-input"
                placeholder="e.g. Port LPN to Kernel"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>

            <div className="nw-identity-secondary">
              <label className="nw-field">
                <span className="nw-lbl">Description <small>optional</small></span>
                <textarea
                  className="nw-textarea"
                  placeholder="What should change?"
                  value={idea}
                  onChange={(e) => setIdea(e.target.value)}
                />
              </label>

              {projects.length > 0 && (
                <label className="nw-field nw-proj-field">
                  <span className="nw-lbl">Project</span>
                  <span className="nw-proj mini-sel">
                    <FolderIcon size={11} />
                    <select
                      value={projectSlug ?? ""}
                      disabled={lockProjectSlug}
                      onChange={(e) => setProjectSlug(e.target.value || null)}
                    >
                      <option value="">Loose work</option>
                      {projects.map((p) => (
                        <option key={p.slug} value={p.slug}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                    <span className="mini-chev" aria-hidden>▾</span>
                  </span>
                </label>
              )}
            </div>
          </div>

          <div className="nw-modelbl">How should this work run?</div>
          <div className="mode-cards">
            {MODE_CARDS.map((card) => (
              <button
                key={card.id}
                type="button"
                className={
                  "mode-card" +
                  (mode === card.id ? " active" : "")
                }
                onClick={() => setMode(card.id)}
              >
                <span className="mc-top">
                  <span className="mc-glyph">{renderModeIcon(card.glyph, 20)}</span>
                  <span className="mc-tag">{card.tag}</span>
                </span>
                <span className="mc-name">{card.title}</span>
                <span className="mc-desc">{card.desc}</span>
              </button>
            ))}
          </div>

          {mode === "planning" ? (
            <div className="empty-prompt">
              <div className="pm-empty-prompt-head">
                <div className="ep-lbl">
                  <DocIcon size={12} /> Plan setup
                </div>
                <label className="pm-framework-select" title={selectedFramework.desc}>
                  <span>Framework</span>
                  <select
                    value={framework}
                    onChange={(e) => setFramework(e.target.value as PlanningFrameworkId)}
                  >
                    {PLANNING_FRAMEWORKS.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="pm-profile-pick">
                <div className="pm-agent-cfg-label">Work type</div>
                <div className="pm-profile-grid">
                  {PLANNING_PROFILES.filter((item) => NEW_WORK_TYPES.has(item.id)).map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={"pm-profile-chip" + (profile === item.id ? " active" : "")}
                      onClick={() => setProfile(item.id)}
                    >
                      <span className="pm-profile-name">{item.name}</span>
                    </button>
                  ))}
                </div>
                <div className="pm-profile-detail">
                  <span className={`pm-profile-depth d-${selectedProfile.depth}`}>
                    {selectedProfile.depth}
                  </span>
                  <span className="pm-profile-desc">{selectedProfile.desc}</span>
                  <span className="pm-profile-artifacts">
                    {selectedProfile.artifacts}
                  </span>
                </div>
              </div>

              <div className="pm-empty-divider" />

              <FolderRow
                icon={<FolderIcon size={15} />}
                label="Work folder"
                value={folder}
                placeholder="Choose a repository or project root"
                onChoose={() => setPicker("work")}
                onClear={clearWorkFolder}
                disabled={submitting}
              />
              <FolderRow
                icon={<DocIcon size={15} />}
                label="Planning files"
                value={planDir}
                placeholder="Where should the framework write its docs?"
                badge={planDir && planDirDefault ? "default" : null}
                onChoose={() => setPicker("plan")}
                onClear={() => {
                  setPlanDir("");
                  setPlanDirDefault(false);
                }}
                disabled={submitting}
              />

              <div className="pm-empty-divider" />

              <div className="pm-agent-cfg">
                <span className="pm-agent-cfg-label">Plan chat</span>
                <PlanningAgentControls
                  value={agentConfig}
                  onChange={setAgentConfig}
                />
              </div>
            </div>
          ) : mode === "loop" ? (
            <>
              <div className="empty-prompt nw-loop-block">
                <div className="nw-loop-head">
                  <div className="ep-lbl">
                    <LoopIcon size={12} /> Loop
                  </div>
                  <label className="nw-loop-search">
                    <SearchIcon size={11} />
                    <input
                      value={loopQuery}
                      onChange={(event) => setLoopQuery(event.target.value)}
                      placeholder="search loops"
                    />
                  </label>
                </div>
                {loopError && <div className="form-error">{loopError}</div>}
                {!loopDefinitions && !loopError && (
                  <div className="nw-loop-loading">Loading loops...</div>
                )}
                <div className="nw-loop-list">
                  {(loopDefinitions ?? [])
                    .filter((definition) =>
                      `${definition.name} ${definition.description} ${definition.scope}`
                        .toLowerCase()
                        .includes(loopQuery.trim().toLowerCase()),
                    )
                    .map((definition) => (
                      <LoopDefinitionChoice
                        key={definition.id}
                        compact
                        definition={definition}
                        query={loopQuery.trim().toLowerCase()}
                        selected={!createLoop && loopDefinitionId === definition.id}
                        onSelect={() => {
                          setCreateLoop(false);
                          setLoopDefinitionId(definition.id);
                        }}
                      />
                    ))}
                  <button
                    type="button"
                    className={"nw-loop-create" + (createLoop ? " active" : "")}
                    onClick={() => {
                      setCreateLoop(true);
                      setLoopDefinitionId("");
                    }}
                  >
                    <span>+</span>
                    <strong>Create a new loop</strong>
                    <small>opens the editor after create</small>
                  </button>
                </div>
                <div className="nw-loop-note">
                  Goal, gate and stages are configured in the loop view
                </div>
              </div>
              <FolderRow
                icon={<FolderIcon size={15} />}
                label="Work folder"
                value={folder}
                placeholder="Choose a repository or project root"
                onChoose={() => setPicker("work")}
                onClear={clearWorkFolder}
                disabled={submitting}
              />
            </>
          ) : (
            <div className="empty-prompt">
              <div className="ep-lbl">
                <AgentIcon size={12} /> Manual
              </div>
              <p className="nw-mode-desc">
                Open the agent canvas for this work and steer agents directly.
              </p>
            </div>
          )}

          {error && <div className="form-error">{error}</div>}
        </div>

        <div className="modal-ft">
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="btn primary" disabled={!canSubmit} onClick={submit}>
            {primaryLabel(submitting)}
          </button>
        </div>
      </div>

      {picker && (
        <FolderPickerDialog
          initialPath={picker === "work" ? folder || null : planDir || folder || null}
          onCancel={() => setPicker(null)}
          onPick={picker === "work" ? pickWorkFolder : pickPlanDir}
        />
      )}
    </div>
  );
}

function FolderRow({
  icon,
  label,
  value,
  placeholder,
  badge,
  disabled,
  onChoose,
  onClear,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  placeholder: string;
  badge?: string | null;
  disabled: boolean;
  onChoose: () => void;
  onClear: () => void;
}) {
  return (
    <div className={"ep-folder" + (value ? " set" : "")}>
      <span className="ef-lbl">{label}</span>
      <span className="ef-val">{value || placeholder}</span>
      {badge && (
        <span className="ef-inherited">
          <CheckIcon size={9} /> {badge}
        </span>
      )}
      {value && (
        <button
          type="button"
          className="ef-clear"
          onClick={onClear}
          disabled={disabled}
          aria-label={`Clear ${label}`}
          title="Clear"
        >
          ×
        </button>
      )}
      <button
        className="btn icon"
        type="button"
        onClick={onChoose}
        disabled={disabled}
        aria-label={`${value ? "Change" : "Choose"} ${label}`}
        title={`${value ? "Change" : "Choose"} ${label}`}
      >
        {icon}
      </button>
    </div>
  );
}

function renderModeIcon(icon: "manual" | "planning" | "loop", size: number) {
  if (icon === "manual") return <PersonIcon size={size} />;
  if (icon === "loop") return <LoopIcon size={size} />;
  return <DocIcon size={size} />;
}

function defaultPlanDir(folder: string): string {
  return folder ? `${folder.replace(/\/+$/, "")}/docs/plan` : "";
}

function primaryLabel(submitting: boolean): string {
  if (submitting) return "Creating...";
  return "Create work";
}
