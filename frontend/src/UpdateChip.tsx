import { useCallback, useEffect, useRef, useState } from "react";
import { getUpdateStatus, type UpdateStatus } from "./api";

// The backend poller runs every 2h; the frontend re-checks every 10
// minutes so a fresh tab catches a "we just became out of date" event
// without making the user reload.
const POLL_INTERVAL_MS = 10 * 60 * 1000;

// Per-tab dismiss. sessionStorage survives in-app navigation but not
// full reload / new tab — that matches "I saw it and want to ignore
// it for now" without becoming permanent.
const DISMISS_KEY = "atelier.update-chip.dismissed-sha";

function readDismissedSha(): string | null {
  try {
    return sessionStorage.getItem(DISMISS_KEY);
  } catch {
    return null;
  }
}

function writeDismissedSha(sha: string): void {
  try {
    sessionStorage.setItem(DISMISS_KEY, sha);
  } catch {
    // Quota / privacy mode — chip just won't stay dismissed. Fine.
  }
}

export function UpdateChip() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [dismissedSha, setDismissedSha] = useState<string | null>(() =>
    readDismissedSha(),
  );
  const popoverRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      getUpdateStatus()
        .then((s) => {
          if (!cancelled) setStatus(s);
        })
        .catch(() => {
          // Backend down or route missing on an older build — keep
          // the chip hidden. No toast; this is informational only.
        });
    };
    tick();
    const id = window.setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // Close popover on outside click.
  useEffect(() => {
    if (!popoverOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (!popoverRef.current) return;
      if (!popoverRef.current.contains(e.target as Node)) {
        setPopoverOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [popoverOpen]);

  const copyCommand = useCallback(async () => {
    if (!status) return;
    const cmd = `cd ${status.repo_path} && claude`;
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API blocked (no HTTPS / no permission). Nothing to
      // gracefully fall back to in a popover; the path is visible so
      // the user can copy by hand.
    }
  }, [status]);

  const dismiss = useCallback(() => {
    if (!status) return;
    writeDismissedSha(status.latest_sha ?? "");
    setDismissedSha(status.latest_sha ?? "");
    setPopoverOpen(false);
  }, [status]);

  if (!status?.available) return null;
  // Dismiss is keyed on the upstream SHA — if a *new* upstream commit
  // lands, the chip reappears even if the user previously dismissed.
  if (status.latest_sha && status.latest_sha === dismissedSha) return null;

  return (
    <div className="update-chip-wrap" ref={popoverRef}>
      <button
        type="button"
        className="update-chip"
        onClick={() => setPopoverOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={popoverOpen}
        title="An update is available"
      >
        <span className="update-chip-dot" aria-hidden />
        Update available
      </button>
      {popoverOpen ? (
        <div className="update-popover" role="dialog" aria-label="Update available">
          <div className="update-popover-hd">An update is available</div>
          <p className="update-popover-body">
            Run <code>/update</code> in Claude — it pulls main, installs
            deps, and runs any pending migrations.
          </p>
          <div className="update-popover-path">
            <span className="update-popover-path-label">Repo</span>
            <code>{status.repo_path}</code>
          </div>
          <div className="update-popover-actions">
            <button
              type="button"
              className="btn primary update-popover-copy"
              onClick={copyCommand}
            >
              {copied ? "Copied" : "Copy: cd <repo> && claude"}
            </button>
            <button
              type="button"
              className="btn-ghost-sm"
              onClick={dismiss}
            >
              Dismiss
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
