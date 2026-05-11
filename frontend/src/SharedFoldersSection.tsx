import { useCallback, useEffect, useState } from "react";

import {
  deleteProjectShare,
  listProjectShares,
  renameProjectShare,
  type SharedFolderSummary,
} from "./api";
import { AddExistingShareDialog } from "./AddExistingShareDialog";
import { NewSharedFolderDialog } from "./NewSharedFolderDialog";

type Props = {
  projectSlug: string;
  projectName: string;
};

/**
 * Project-scoped shared folders section, rendered below the works
 * section on the project page.
 *
 * Each share row shows: label · mount path · "custom" badge when the
 * real location differs from the canonical location · kebab menu
 * (Rename, Reveal in Finder, Stop sharing, Delete folder contents).
 *
 * "Stop sharing" removes the Atelier registration + symlink only;
 * "Delete folder contents" additionally wipes the canonical contents
 * and is refused server-side for custom-location shares.
 */
export function SharedFoldersSection({ projectSlug, projectName }: Props) {
  const [shares, setShares] = useState<SharedFolderSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newDialogOpen, setNewDialogOpen] = useState(false);
  const [existingDialogOpen, setExistingDialogOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setShares(await listProjectShares(projectSlug));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectSlug]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleCreated = (created: SharedFolderSummary) => {
    setShares((curr) => (curr ? [...curr, created] : [created]));
    setNewDialogOpen(false);
    setExistingDialogOpen(false);
  };

  const handleRename = async (share: SharedFolderSummary) => {
    const next = window.prompt("Rename shared folder", share.name);
    if (next === null || !next.trim()) return;
    try {
      const updated = await renameProjectShare(
        projectSlug,
        share.slug,
        next.trim(),
      );
      setShares((curr) =>
        (curr ?? []).map((s) => (s.slug === share.slug ? updated : s)),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const handleStopSharing = async (share: SharedFolderSummary) => {
    const dest = share.is_custom_location
      ? `Your folder at ${share.real_path} is left untouched.`
      : `Data under ${share.canonical_path} stays on disk — re-adopt later if you want it back.`;
    if (
      !window.confirm(
        `Stop sharing "${share.name}"?\n\n` +
          `This removes the Atelier registration and unlinks it from future agents.\n${dest}`,
      )
    ) {
      return;
    }
    try {
      await deleteProjectShare(projectSlug, share.slug, false);
      setShares((curr) => (curr ?? []).filter((s) => s.slug !== share.slug));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const handleDeleteContents = async (share: SharedFolderSummary) => {
    if (
      !window.confirm(
        `DELETE folder contents for "${share.name}"?\n\n` +
          `This permanently removes:\n  ${share.canonical_path}\n\nThis cannot be undone.`,
      )
    ) {
      return;
    }
    try {
      await deleteProjectShare(projectSlug, share.slug, true);
      setShares((curr) => (curr ?? []).filter((s) => s.slug !== share.slug));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section className="shared-folders">
      <header className="shared-folders-hd">
        <h2>
          Shared folders{" "}
          {shares !== null && (
            <span className="count mono">{shares.length}</span>
          )}
        </h2>
        <div className="spacer" />
        <button className="btn" onClick={() => setNewDialogOpen(true)}>
          + New
        </button>
        <button className="btn" onClick={() => setExistingDialogOpen(true)}>
          + Add existing
        </button>
      </header>

      {error && <div className="form-error">{error}</div>}

      {shares !== null && shares.length === 0 && (
        <div className="shared-folders-empty hint">
          No shared folders yet. Create one for stuff that outlives
          individual agents — planning notes, BMAD outputs, scratch files
          you want any agent in this project to see without committing it
          to git.
        </div>
      )}

      {shares !== null && shares.length > 0 && (
        <ul className="shared-folders-list">
          {shares.map((share) => (
            <ShareRow
              key={share.slug}
              share={share}
              onRename={() => handleRename(share)}
              onStopSharing={() => handleStopSharing(share)}
              onDeleteContents={() => handleDeleteContents(share)}
            />
          ))}
        </ul>
      )}

      {newDialogOpen && (
        <NewSharedFolderDialog
          projectSlug={projectSlug}
          projectName={projectName}
          onClose={() => setNewDialogOpen(false)}
          onCreated={handleCreated}
        />
      )}
      {existingDialogOpen && (
        <AddExistingShareDialog
          projectSlug={projectSlug}
          projectName={projectName}
          onClose={() => setExistingDialogOpen(false)}
          onCreated={handleCreated}
        />
      )}
    </section>
  );
}

function ShareRow({
  share,
  onRename,
  onStopSharing,
  onDeleteContents,
}: {
  share: SharedFolderSummary;
  onRename: () => void;
  onStopSharing: () => void;
  onDeleteContents: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  // Close menu on outside click (cheap; no portal).
  useEffect(() => {
    if (!menuOpen) return;
    const handler = () => setMenuOpen(false);
    window.addEventListener("click", handler);
    return () => window.removeEventListener("click", handler);
  }, [menuOpen]);

  return (
    <li className="shared-folder-row">
      <span className="shared-folder-glyph" aria-hidden>
        📁
      </span>
      <div className="shared-folder-main">
        <div className="shared-folder-name">
          {share.name}
          {share.is_custom_location && (
            <span className="shared-folder-badge" title="Custom location">
              custom
            </span>
          )}
        </div>
        <div className="shared-folder-paths mono">
          <span>./{share.mount_path}/</span>
          <span className="shared-folder-real">
            {share.is_custom_location ? share.real_path : share.canonical_path}
          </span>
        </div>
      </div>
      <div className="shared-folder-actions">
        <button
          className="btn-icon"
          aria-label="More"
          title="More"
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((v) => !v);
          }}
        >
          ⋯
        </button>
        {menuOpen && (
          <div className="shared-folder-menu" onClick={(e) => e.stopPropagation()}>
            <button
              className="menu-item"
              onClick={() => {
                setMenuOpen(false);
                onRename();
              }}
            >
              Rename
            </button>
            <button
              className="menu-item"
              onClick={() => {
                setMenuOpen(false);
                onStopSharing();
              }}
            >
              Stop sharing
            </button>
            <button
              className="menu-item danger"
              disabled={share.is_custom_location}
              title={
                share.is_custom_location
                  ? "Disabled for custom-location shares — Atelier never deletes data it doesn't own"
                  : undefined
              }
              onClick={() => {
                setMenuOpen(false);
                onDeleteContents();
              }}
            >
              Delete folder contents
            </button>
          </div>
        )}
      </div>
    </li>
  );
}
