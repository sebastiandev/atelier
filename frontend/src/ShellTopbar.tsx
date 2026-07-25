import { type CSSProperties, type ReactNode } from "react";

import { BrandMark } from "./BrandMark";
import { SearchIcon, SlidersIcon } from "./Icons";
import { ThemeToggle } from "./ThemeToggle";

export type ShellTopbarCrumb = {
  href?: string;
  hue?: number;
  label: string;
  onClick?: () => void;
};

export type ShellTopbarView = {
  detail?: ReactNode;
  inline?: boolean;
  title: ReactNode;
};

type ShellTopbarProps = {
  crumbs?: ShellTopbarCrumb[];
  primaryAction?: ReactNode;
  showUtilities?: boolean;
  view?: ShellTopbarView;
};

/** Shared app topbar with navigation breadcrumbs and global utilities. */
export function ShellTopbar({
  crumbs = [],
  primaryAction,
  showUtilities = true,
  view,
}: ShellTopbarProps) {
  return (
    <header className={"shell-topbar" + (view ? " has-view" : "")}>
      <div className="topbar-origin">
        <a className="topbar-wordmark" href="/" title="Back to Atelier">
          <span aria-hidden><BrandMark /></span>
          <strong>Atelier</strong>
        </a>
        <nav className="topbar-crumbs" aria-label="Breadcrumb">
          {crumbs.map((crumb, index) => (
            <span className="topbar-crumb-wrap" key={`${crumb.label}-${index}`}>
              <span className="topbar-sep">/</span>
              {crumb.href ? (
                <a
                  className={crumb.hue === undefined ? undefined : "topbar-project-chip"}
                  href={crumb.href}
                  style={crumb.hue === undefined ? undefined : ({ "--proj-h": crumb.hue } as CSSProperties)}
                >
                  {crumb.hue !== undefined && <i />}
                  {crumb.label}
                </a>
              ) : crumb.onClick ? (
                <button type="button" onClick={crumb.onClick}>
                  {crumb.label}
                </button>
              ) : (
                <span className="topbar-current" aria-current={index === crumbs.length - 1 ? "page" : undefined}>
                  {crumb.label}
                </span>
              )}
            </span>
          ))}
        </nav>
      </div>
      {view && (
        <div className={"topbar-view" + (view.inline ? " inline" : "")}>
          <div className="topbar-view-heading">
            <span className="topbar-view-title">{view.title}</span>
          </div>
          {view.detail != null && <span className="topbar-view-detail">{view.detail}</span>}
        </div>
      )}
      <div className="topbar-actions">
        {primaryAction}
        {showUtilities && (
          <>
            <button
              className="btn ghost icon sm"
              onClick={() => window.dispatchEvent(new Event("atelier:open-search"))}
              title="Search (Cmd/Ctrl+K)"
              aria-label="Search"
            >
              <SearchIcon size={12} />
            </button>
            <a className="btn ghost icon sm" href="/settings" title="Settings" aria-label="Settings">
              <SlidersIcon size={12} />
            </a>
            <ThemeToggle className="btn ghost icon sm" />
          </>
        )}
      </div>
    </header>
  );
}
