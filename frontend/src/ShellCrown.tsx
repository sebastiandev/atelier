import { BrandMark } from "./BrandMark";
import { SearchIcon, SlidersIcon } from "./Icons";
import { ThemeToggle } from "./ThemeToggle";

type ShellCrownProps = {
  onSearch?: () => void;
  searchHref?: string;
  showActions?: boolean;
};

/** Shared left-rail crown with the Atelier wordmark and shell actions. */
export function ShellCrown({
  onSearch,
  searchHref = "/",
  showActions = true,
}: ShellCrownProps) {
  return (
    <div className="crown">
      <a className="wordmark" href="/" title="Back to workspace">
        <span className="wm-mark" aria-hidden>
          <BrandMark />
        </span>
        <span className="wm-rest">telier</span>
      </a>
      {showActions && (
        <div className="crown-actions">
          {onSearch ? (
            <button
              type="button"
              className="btn-icon"
              onClick={onSearch}
              title="Search (⇧F)"
              aria-label="Search"
            >
              <SearchIcon size={12} />
            </button>
          ) : (
            <a className="btn-icon" href={searchHref} title="Search" aria-label="Search">
              <SearchIcon size={12} />
            </a>
          )}
          <a
            className="btn-icon"
            href="/settings"
            title="Settings (⌘,)"
            aria-label="Settings"
          >
            <SlidersIcon size={12} />
          </a>
          <ThemeToggle className="btn-icon" />
        </div>
      )}
    </div>
  );
}
