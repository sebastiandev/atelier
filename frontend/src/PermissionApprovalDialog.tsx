import { useEffect, useMemo, useRef, useState } from "react";

import type {
  PendingPermission,
  PermissionDecision,
} from "./useAgentStream";

type ApprovalStatus = "pending" | "allowed" | "rejected";
type ResolvedPrompt = {
  prompt: PendingPermission;
  status: Exclude<ApprovalStatus, "pending">;
};
type DisplayPrompt = {
  prompt: PendingPermission;
  status: ApprovalStatus;
};
type PermissionDescriptor = {
  tool: string;
  arg: string;
  description: string;
  detail: string;
  summary: string;
  patterns: string[];
};

const RESOLVED_HOLD_MS = 680;
const KNOWN_TOOL_NAMES = [
  "Bash",
  "Edit",
  "FileChange",
  "Glob",
  "Grep",
  "MultiEdit",
  "Read",
  "Skill",
  "ToolSearch",
  "WebFetch",
  "WebSearch",
  "Workflow",
  "Write",
];

export function PermissionApprovalDialog({
  pendingPermissions,
  onDecide,
}: {
  pendingPermissions: PendingPermission[];
  onDecide: (
    requestId: string,
    decision: PermissionDecision,
  ) => boolean | void;
}) {
  const [resolved, setResolved] = useState<Record<string, ResolvedPrompt>>({});
  const [singleDetailsOpen, setSingleDetailsOpen] = useState(false);
  const timersRef = useRef<Record<string, number>>({});
  const pendingRef = useRef(pendingPermissions);

  useEffect(() => {
    pendingRef.current = pendingPermissions;
  }, [pendingPermissions]);

  useEffect(() => {
    const pendingIds = new Set(pendingPermissions.map((p) => p.request_id));
    for (const requestId of Object.keys(resolved)) {
      const timer = timersRef.current[requestId];
      if (pendingIds.has(requestId)) {
        if (timer !== undefined) {
          window.clearTimeout(timer);
          delete timersRef.current[requestId];
        }
        continue;
      }
      if (timer !== undefined) continue;
      timersRef.current[requestId] = window.setTimeout(() => {
        if (pendingRef.current.some((p) => p.request_id === requestId)) return;
        setResolved((prev) => {
          if (!(requestId in prev)) return prev;
          const next = { ...prev };
          delete next[requestId];
          return next;
        });
        delete timersRef.current[requestId];
      }, RESOLVED_HOLD_MS);
    }
  }, [pendingPermissions, resolved]);

  useEffect(() => {
    return () => {
      for (const timer of Object.values(timersRef.current)) {
        window.clearTimeout(timer);
      }
      timersRef.current = {};
    };
  }, []);

  const displayPrompts = useMemo<DisplayPrompt[]>(() => {
    const byId = new Map<string, DisplayPrompt>();
    for (const prompt of pendingPermissions) {
      byId.set(prompt.request_id, {
        prompt,
        status: resolved[prompt.request_id]?.status ?? "pending",
      });
    }
    for (const entry of Object.values(resolved)) {
      if (!byId.has(entry.prompt.request_id)) {
        byId.set(entry.prompt.request_id, entry);
      }
    }
    return [...byId.values()].sort((a, b) => a.prompt.seq - b.prompt.seq);
  }, [pendingPermissions, resolved]);

  useEffect(() => {
    setSingleDetailsOpen(false);
  }, [displayPrompts.length === 1 ? displayPrompts[0]?.prompt.request_id : ""]);

  const pendingPrompts = displayPrompts.filter((p) => p.status === "pending");
  const mode =
    displayPrompts.length === 0
      ? "idle"
      : displayPrompts.length === 1
        ? "single"
        : "group";

  function decide(
    prompt: PendingPermission,
    decision: PermissionDecision,
    status: Exclude<ApprovalStatus, "pending">,
  ) {
    if (resolved[prompt.request_id]) return;
    const sent = onDecide(prompt.request_id, decision);
    if (sent === false) return;
    setResolved((prev) => ({
      ...prev,
      [prompt.request_id]: { prompt, status },
    }));
  }

  function allow(prompt: PendingPermission) {
    decide(prompt, allowDecision(prompt), "allowed");
  }

  function reject(prompt: PendingPermission) {
    decide(prompt, "deny", "rejected");
  }

  function allowAlways(prompt: PendingPermission) {
    decide(prompt, "allow_always", "allowed");
  }

  function allowAll() {
    for (const item of pendingPrompts) allow(item.prompt);
  }

  function rejectAll() {
    for (const item of pendingPrompts) reject(item.prompt);
  }

  useEffect(() => {
    if (mode === "idle") return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (mode === "single") {
        const item = displayPrompts[0];
        if (!item || item.status !== "pending") return;
        if (e.key === "Escape") {
          e.preventDefault();
          reject(item.prompt);
        } else if (e.key.toLowerCase() === "a" && e.shiftKey) {
          if (!hasOptionKind(item.prompt, "allow_always")) return;
          e.preventDefault();
          allowAlways(item.prompt);
        } else if (e.key.toLowerCase() === "a" && !e.shiftKey) {
          e.preventDefault();
          allow(item.prompt);
        }
      } else if (mode === "group") {
        if (e.key === "Escape") {
          e.preventDefault();
          rejectAll();
        } else if (e.key.toLowerCase() === "a" && e.shiftKey) {
          e.preventDefault();
          allowAll();
        }
      }
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [displayPrompts, mode, pendingPrompts, resolved]);

  if (mode === "idle") return null;

  return (
    <div className="permission-approval-slot">
      {mode === "single" ? (
        <SingleApprovalCard
          item={displayPrompts[0]}
          detailsOpen={singleDetailsOpen}
          onToggleDetails={() => setSingleDetailsOpen((v) => !v)}
          onAllow={() => allow(displayPrompts[0].prompt)}
          onAllowAlways={() => allowAlways(displayPrompts[0].prompt)}
          onReject={() => reject(displayPrompts[0].prompt)}
        />
      ) : (
        <GroupedApprovalCard
          items={displayPrompts}
          pendingCount={pendingPrompts.length}
          onAllow={allow}
          onReject={reject}
          onAllowAll={allowAll}
          onRejectAll={rejectAll}
        />
      )}
    </div>
  );
}

function SingleApprovalCard({
  item,
  detailsOpen,
  onToggleDetails,
  onAllow,
  onAllowAlways,
  onReject,
}: {
  item: DisplayPrompt;
  detailsOpen: boolean;
  onToggleDetails: () => void;
  onAllow: () => void;
  onAllowAlways: () => void;
  onReject: () => void;
}) {
  const prompt = item.prompt;
  const request = describePermission(prompt);
  const resolvedStatus = item.status === "pending" ? null : item.status;
  const resolved = resolvedStatus !== null;
  return (
    <div
      className={
        "permission-approval-card enter" +
        (resolved ? ` resolved ${item.status}` : "")
      }
      role="alertdialog"
      aria-label={`Permission for ${request.tool}`}
    >
      <button
        type="button"
        className="permission-approval-head"
        onClick={onToggleDetails}
        disabled={resolved}
      >
        <LockIcon />
        <span className="permission-approval-question">
          Allow <b>{request.tool}</b>?
        </span>
        {request.arg && (
          <span className="permission-approval-target">{request.arg}</span>
        )}
        <span className="spacer" />
        {!resolved && (
          <span className="permission-approval-details-toggle">
            {detailsOpen ? "Hide details" : "Show details"}
            <ChevronIcon open={detailsOpen} />
          </span>
        )}
      </button>

      <div className="permission-approval-body">
        {detailsOpen ? (
          <pre className="permission-approval-command">
            <span aria-hidden>$ </span>
            {request.detail}
          </pre>
        ) : (
          <div className="permission-approval-summary">{request.summary}</div>
        )}
      </div>

      {resolved ? (
        <div className="permission-approval-actions">
          <ResolutionLabel status={resolvedStatus} long />
        </div>
      ) : (
        <>
          <div className="permission-approval-actions">
            <button
              type="button"
              className="permission-approval-btn reject"
              onClick={onReject}
              title={optionLabel(prompt, "reject_once") ?? undefined}
            >
              Reject
              <Kbd>esc</Kbd>
            </button>
            <span className="spacer" />
            <div className="permission-approval-affirm">
              <button
                type="button"
                className="permission-approval-btn allow"
                onClick={onAllow}
                title={optionLabel(prompt, "allow_once") ?? undefined}
              >
                Allow
                <Kbd>A</Kbd>
              </button>
              {hasOptionKind(prompt, "allow_always") && (
                <button
                  type="button"
                  className="permission-approval-btn always"
                  onClick={onAllowAlways}
                  title={optionLabel(prompt, "allow_always") ?? undefined}
                >
                  Always allow
                  <Kbd>⇧A</Kbd>
                </button>
              )}
            </div>
          </div>
          {hasOptionKind(prompt, "allow_always") && (
            <div className="permission-approval-note">
              Always allow adds
              {request.patterns.map((pattern) => (
                <code key={pattern}>{pattern}</code>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function GroupedApprovalCard({
  items,
  pendingCount,
  onAllow,
  onReject,
  onAllowAll,
  onRejectAll,
}: {
  items: DisplayPrompt[];
  pendingCount: number;
  onAllow: (prompt: PendingPermission) => void;
  onReject: (prompt: PendingPermission) => void;
  onAllowAll: () => void;
  onRejectAll: () => void;
}) {
  return (
    <div
      className="permission-approval-card group enter"
      role="alertdialog"
      aria-label={`${pendingCount} tools need approval`}
    >
      <div className="permission-approval-group-head">
        <LockIcon size={14} />
        <span className="permission-approval-group-title">
          {pendingCount} tool{pendingCount === 1 ? "" : "s"} need approval
        </span>
        <span className="spacer" />
        <button
          type="button"
          className="permission-approval-btn allow sm"
          disabled={pendingCount === 0}
          onClick={onAllowAll}
        >
          Allow all
          <Kbd>⇧A</Kbd>
        </button>
        <button
          type="button"
          className="permission-approval-btn reject sm"
          disabled={pendingCount === 0}
          onClick={onRejectAll}
        >
          Reject all
        </button>
      </div>
      <div className="permission-approval-list">
        {items.map((item) => {
          const prompt = item.prompt;
          const request = describePermission(prompt);
          return (
            <div
              key={prompt.request_id}
              className={
                "permission-approval-row" +
                (item.status !== "pending" ? ` done ${item.status}` : "")
              }
            >
              <LockIcon size={12} muted />
              <span className="permission-approval-row-tool">
                {request.tool}
              </span>
              {request.arg && (
                <code className="permission-approval-row-arg">{request.arg}</code>
              )}
              <span className="permission-approval-row-desc">
                {request.description}
              </span>
              <span className="spacer" />
              {item.status !== "pending" ? (
                <ResolutionLabel status={item.status} />
              ) : (
                <span className="permission-approval-row-actions">
                  <button
                    type="button"
                    className="permission-approval-icon-btn allow"
                    onClick={() => onAllow(prompt)}
                    aria-label={`Allow ${request.tool}`}
                    title={optionLabel(prompt, "allow_once") ?? "Allow"}
                  >
                    <CheckIcon />
                  </button>
                  <button
                    type="button"
                    className="permission-approval-icon-btn reject"
                    onClick={() => onReject(prompt)}
                    aria-label={`Reject ${request.tool}`}
                    title={optionLabel(prompt, "reject_once") ?? "Reject"}
                  >
                    <CrossIcon />
                  </button>
                </span>
              )}
            </div>
          );
        })}
      </div>
      <div className="permission-approval-group-foot">
        Each decision is sent to the CLI on its own - batching is a
        convenience, not a merge.
      </div>
    </div>
  );
}

function ResolutionLabel({
  status,
  long = false,
}: {
  status: Exclude<ApprovalStatus, "pending">;
  long?: boolean;
}) {
  return (
    <span
      className={
        "permission-approval-resolution " +
        (status === "allowed" ? "allowed" : "rejected")
      }
    >
      {status === "allowed" ? <CheckIcon /> : <CrossIcon />}
      {status === "allowed"
        ? long
          ? "Allowed - agent continuing"
          : "Allowed"
        : "Rejected"}
    </span>
  );
}

function Kbd({ children }: { children: string }) {
  return <span className="permission-approval-kbd">{children}</span>;
}

function describePermission(prompt: PendingPermission): PermissionDescriptor {
  const arg = primaryArgument(prompt);
  const tool = displayToolName(prompt, arg);
  const description = permissionDescription(prompt, tool);
  const detail = detailText(prompt, tool, arg);
  const target = arg ? ` ${arg}` : "";
  return {
    tool,
    arg,
    description,
    detail,
    summary: `${tool} wants to ${description}${target}.`,
    patterns: permissionPatterns(tool, arg),
  };
}

function primaryArgument(prompt: PendingPermission): string {
  const input = prompt.tool_input;
  for (const key of [
    "command",
    "file_path",
    "path",
    "url",
    "pattern",
    "query",
    "name",
    "skill",
  ]) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  for (const [key, value] of Object.entries(input)) {
    if (typeof value === "string" && value.trim()) {
      return `${key}: ${value.trim()}`;
    }
  }
  return "";
}

function displayToolName(prompt: PendingPermission, arg: string): string {
  const optionTool = toolNameFromOptions(prompt.options);
  const directTool = canonicalToolName(prompt.tool_name);
  const cleanName = stripOuterQuotes(prompt.tool_name.trim());
  const cleanArg = stripOuterQuotes(arg.trim());

  if (directTool) return directTool;
  if (optionTool && isLikelyActionTitle(prompt.tool_name, cleanName, cleanArg)) {
    return optionTool;
  }
  if (optionTool && cleanName.toLowerCase() === cleanArg.toLowerCase()) {
    return optionTool;
  }
  return cleanName || optionTool || "(unknown)";
}

function canonicalToolName(value: string): string | null {
  const clean = stripOuterQuotes(value.trim());
  if (!clean) return null;
  if (KNOWN_TOOL_NAMES.includes(clean)) return clean;
  const first = clean.split(/\s+/, 1)[0];
  if (KNOWN_TOOL_NAMES.includes(first)) return first;
  return null;
}

function toolNameFromOptions(
  options: PendingPermission["options"] | undefined,
): string | null {
  const always = options?.find((option) => option.kind === "allow_always");
  const label = always?.name.trim();
  if (!label) return null;
  const match =
    /^Always Allow(?: all)? ([A-Za-z][A-Za-z0-9_]*(?:__[A-Za-z0-9_]+)?)(?:\(|$)/.exec(
      label,
    );
  return match ? match[1] : null;
}

function isLikelyActionTitle(
  rawName: string,
  cleanName: string,
  cleanArg: string,
): boolean {
  if (!cleanName) return false;
  if (rawName.trim().startsWith("\"") || rawName.trim().startsWith("'")) {
    return true;
  }
  if (cleanArg && cleanName.toLowerCase() === cleanArg.toLowerCase()) return true;
  return /\s/.test(cleanName);
}

function stripOuterQuotes(value: string): string {
  if (
    value.length >= 2 &&
    ((value.startsWith("\"") && value.endsWith("\"")) ||
      (value.startsWith("'") && value.endsWith("'")))
  ) {
    return value.slice(1, -1);
  }
  return value;
}

function permissionDescription(
  prompt: PendingPermission,
  tool: string,
): string {
  const input = prompt.tool_input;
  switch (tool) {
    case "Bash":
      return "run shell command";
    case "ToolSearch":
      return "look up available tools";
    case "WebSearch":
      return "search the web for";
    case "WebFetch":
      return "fetch one page";
    case "Write": {
      const content = typeof input.content === "string" ? input.content : "";
      return content ? `write file - ${formatBytes(content.length)}` : "write file";
    }
    case "Edit":
      return "edit file";
    case "MultiEdit": {
      const edits = Array.isArray(input.edits) ? input.edits.length : 0;
      return edits ? `edit file - ${edits} edits` : "edit file";
    }
    case "Read":
      return "read file";
    case "Grep":
      return "search files";
    case "Glob":
      return "match files";
    case "Workflow":
      return "run workflow";
    case "Skill":
      return "run skill";
    default:
      return "run tool";
  }
}

function detailText(prompt: PendingPermission, tool: string, arg: string): string {
  if (tool === "Bash") return arg || "(empty command)";
  if (Object.keys(prompt.tool_input).length === 0) {
    return `${tool}${arg ? ` ${arg}` : ""}`;
  }
  return JSON.stringify(prompt.tool_input, null, 2);
}

function permissionPatterns(tool: string, arg: string): string[] {
  if (!arg) return [`${tool}(*)`];
  const shortArg = arg.length > 52 ? `${arg.slice(0, 49)}...` : arg;
  return [`${tool}(${shortArg})`, `${tool}(*)`];
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} kB`;
}

function optionLabel(prompt: PendingPermission, kind: string): string | null {
  const match = prompt.options?.find((o) => o.kind === kind);
  return match ? match.name : null;
}

function hasOptionKind(prompt: PendingPermission, kind: string): boolean {
  if (!prompt.options || prompt.options.length === 0) return true;
  return prompt.options.some((o) => o.kind === kind);
}

function allowDecision(prompt: PendingPermission): PermissionDecision {
  if (hasOptionKind(prompt, "allow_once")) return "allow";
  if (hasOptionKind(prompt, "allow_always")) return "allow_always";
  return "allow";
}

function LockIcon({
  size = 15,
  muted = false,
}: {
  size?: number;
  muted?: boolean;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={muted ? "permission-approval-icon muted" : "permission-approval-icon"}
      aria-hidden
    >
      <rect x="4.5" y="10.5" width="15" height="10" rx="2.2" />
      <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      className={open ? "permission-approval-chevron open" : "permission-approval-chevron"}
      aria-hidden
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M5 12.5l4.5 4.5L19 6.5"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CrossIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M6 6l12 12M18 6L6 18"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  );
}
