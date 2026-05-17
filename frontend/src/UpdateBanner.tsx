import { useCallback, useEffect, useRef, useState } from "react";

import { getUpdateStatus, type UpdateStatus } from "./api";

// Re-check every 10 minutes from the FE; the backend poller runs
// every 2h. A fresh tab opened mid-day will catch a "we just became
// out of date" event without the user reloading.
const POLL_INTERVAL_MS = 10 * 60 * 1000;

// Per-tab dismiss. sessionStorage survives in-app navigation but not
// full reload / new tab — matches "I saw it, hide it for now"
// without becoming permanent. Keyed on the upstream SHA so a NEW
// upstream commit re-surfaces the banner even if previously
// dismissed.
const DISMISS_KEY = "atelier.update-banner.dismissed-sha";

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
    // Quota / privacy mode — banner just won't stay dismissed. Fine.
  }
}

// Top-of-screen banner. Mounts once in <App>; renders nothing until
// the backend confirms an update is available AND the SHA hasn't
// been dismissed this session. Click expands a popover with the
// repo path + a one-click "copy cd <repo> && claude" action.
export function UpdateBanner() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [dismissedSha, setDismissedSha] = useState<string | null>(() =>
    readDismissedSha(),
  );
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      getUpdateStatus()
        .then((s) => {
          if (!cancelled) setStatus(s);
        })
        .catch(() => {
          // Backend down / route missing on older build — keep the
          // banner hidden. No toast; informational only.
        });
    };
    tick();
    const id = window.setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!popoverOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target as Node)) {
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
      // Clipboard API blocked (no HTTPS / no permission). The path
      // is visible in the popover so the user can copy by hand.
    }
  }, [status]);

  const dismiss = useCallback(() => {
    if (!status) return;
    writeDismissedSha(status.latest_sha ?? "");
    setDismissedSha(status.latest_sha ?? "");
    setPopoverOpen(false);
  }, [status]);

  if (!status?.available) return null;
  if (status.latest_sha && status.latest_sha === dismissedSha) return null;

  return (
    <div className="update-banner-wrap" ref={wrapRef}>
      <button
        type="button"
        className="update-banner"
        onClick={() => setPopoverOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={popoverOpen}
        title="An update is available"
      >
        <span className="update-banner-dot" aria-hidden />
        <span className="update-banner-msg">An update is available</span>
        <span className="update-banner-hint">click for details</span>
        <button
          type="button"
          className="update-banner-x"
          aria-label="Dismiss"
          onClick={(e) => {
            e.stopPropagation();
            dismiss();
          }}
        >
          ×
        </button>
      </button>
      {popoverOpen && (
        <div
          className="update-banner-popover"
          role="dialog"
          aria-label="Update available"
        >
          <p className="update-banner-popover-body">
            Run <code>/update</code> in Claude — it pulls main,
            installs deps, and runs any pending migrations.
          </p>
          <div className="update-banner-popover-path">
            <span className="update-banner-popover-path-label">Repo</span>
            <code>{status.repo_path}</code>
          </div>
          <div className="update-banner-popover-actions">
            <button
              type="button"
              className="btn primary"
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
      )}
    </div>
  );
}
