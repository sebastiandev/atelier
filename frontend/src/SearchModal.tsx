import { useEffect, useMemo, useRef, useState } from "react";

import type { ProjectSummary, WorkSummary } from "./api";
import { CheckIcon, SearchIcon } from "./Icons";

export type SearchScope = "all" | { slug: string };

type Result =
  | { kind: "project"; project: ProjectSummary }
  | { kind: "work"; work: WorkSummary; project: ProjectSummary | null };

type Props = {
  works: WorkSummary[];
  projects: ProjectSummary[];
  defaultScope?: SearchScope;
  onClose: () => void;
};

// Keyboard-driven search overlay. ↑/↓ navigate, Enter opens, Esc
// closes. Auto-focuses the input; matches highlight via <mark>.
export function SearchModal({
  works,
  projects,
  defaultScope = "all",
  onClose,
}: Props) {
  const [q, setQ] = useState("");
  const [scope, setScope] = useState<SearchScope>(defaultScope);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const projectMap = useMemo(() => {
    const m = new Map<string, ProjectSummary>();
    for (const p of projects) m.set(p.slug, p);
    return m;
  }, [projects]);

  const results = useMemo<{
    projects: ProjectSummary[];
    works: WorkSummary[];
  }>(() => {
    const term = q.trim().toLowerCase();
    const inScope = (slug: string | null | undefined) => {
      if (scope === "all") return true;
      return slug === scope.slug;
    };
    const projList = projects.filter((p) => {
      if (scope !== "all" && p.slug !== scope.slug) return false;
      if (!term) return scope === "all";
      return [p.name, p.slug, p.description].some(
        (s) => s != null && s.toLowerCase().includes(term),
      );
    });
    const workList = works.filter((w) => {
      if (!inScope(w.project_slug)) return false;
      if (!term) return true;
      return [w.slug, w.name, w.description].some(
        (s) => s != null && s.toLowerCase().includes(term),
      );
    });
    return { projects: projList.slice(0, 8), works: workList.slice(0, 20) };
  }, [q, scope, works, projects]);

  const flat = useMemo<Result[]>(
    () => [
      ...results.projects.map<Result>((project) => ({
        kind: "project",
        project,
      })),
      ...results.works.map<Result>((work) => ({
        kind: "work",
        work,
        project: work.project_slug
          ? projectMap.get(work.project_slug) ?? null
          : null,
      })),
    ],
    [results, projectMap],
  );

  useEffect(() => {
    setActive(0);
  }, [q, scope]);

  function openResult(r: Result) {
    if (r.kind === "project") {
      window.location.assign(`/projects/${r.project.slug}`);
    } else {
      window.location.assign(`/works/${r.work.slug}`);
    }
    onClose();
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      onClose();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, Math.max(flat.length - 1, 0)));
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const r = flat[active];
      if (r) openResult(r);
    }
  }

  const scopeProject =
    scope !== "all" ? projectMap.get(scope.slug) ?? null : null;

  return (
    <div
      className="search-scrim"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={onKey}
      tabIndex={-1}
    >
      <div className="search-modal" role="dialog" aria-label="Search">
        <div className="search-input-row">
          <span className="ico">
            <SearchIcon size={14} />
          </span>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            placeholder="search work units, projects, ids…"
          />
          {scopeProject && (
            <div className="scope-toggle" role="tablist">
              <button
                className={scope === "all" ? "active" : ""}
                onClick={() => setScope("all")}
              >
                all
              </button>
              <button
                className={scope !== "all" ? "active" : ""}
                onClick={() => setScope({ slug: scopeProject.slug })}
              >
                {scopeProject.slug}
              </button>
            </div>
          )}
          <span className="esc-tag">esc</span>
        </div>

        <div className="search-results">
          {flat.length === 0 && (
            <div className="search-empty">
              <span className="strong">no matches</span>
              try a different term, or broaden the scope
            </div>
          )}

          {results.projects.length > 0 && (
            <>
              <div className="search-group-hd">
                <span>Projects</span>
                <span className="count">{results.projects.length}</span>
              </div>
              {results.projects.map((p, i) => (
                <button
                  key={p.slug}
                  className={
                    "search-result" + (active === i ? " kbd-active" : "")
                  }
                  style={{
                    ["--proj-color" as string]: `oklch(0.62 0.16 ${p.color})`,
                    ["--proj-soft" as string]: `oklch(0.62 0.16 ${p.color} / 0.12)`,
                  }}
                  onMouseEnter={() => setActive(i)}
                  onClick={() =>
                    openResult({ kind: "project", project: p })
                  }
                >
                  <span className="pip">{p.glyph}</span>
                  <span className="id">{p.slug}</span>
                  <span className="body">
                    <div className="name">{highlight(p.name, q)}</div>
                    <div className="desc">
                      {highlight(p.description, q)}
                    </div>
                  </span>
                  <span className="tail">
                    {
                      works.filter((w) => w.project_slug === p.slug).length
                    }{" "}
                    work
                  </span>
                </button>
              ))}
            </>
          )}

          {results.works.length > 0 && (
            <>
              <div className="search-group-hd">
                <span>Work units</span>
                <span className="count">{results.works.length}</span>
              </div>
              {results.works.map((w, i) => {
                const flatIdx = results.projects.length + i;
                const p = w.project_slug
                  ? projectMap.get(w.project_slug) ?? null
                  : null;
                return (
                  <button
                    key={w.slug}
                    className={
                      "search-result" +
                      (active === flatIdx ? " kbd-active" : "")
                    }
                    style={
                      p
                        ? {
                            ["--proj-color" as string]: `oklch(0.62 0.16 ${p.color})`,
                            ["--proj-soft" as string]: `oklch(0.62 0.16 ${p.color} / 0.12)`,
                          }
                        : undefined
                    }
                    onMouseEnter={() => setActive(flatIdx)}
                    onClick={() =>
                      openResult({ kind: "work", work: w, project: p })
                    }
                  >
                    <span
                      className="pip"
                      style={
                        !p
                          ? {
                              background: "var(--bg-2)",
                              color: "var(--fg-3)",
                              borderColor: "var(--line)",
                            }
                          : undefined
                      }
                    >
                      {w.status === "active" ? (
                        <span className="dot live" />
                      ) : (
                        <CheckIcon size={9} />
                      )}
                    </span>
                    <span className="id">{w.slug}</span>
                    <span className="body">
                      <div className="name">{highlight(w.name, q)}</div>
                      <div className="desc">
                        {highlight(w.description, q)}
                      </div>
                    </span>
                    <span className="tail">
                      {p ? p.name : "loose"}
                    </span>
                  </button>
                );
              })}
            </>
          )}
        </div>

        <div className="search-foot">
          <span className="seg">
            <span className="key">↑</span>
            <span className="key">↓</span> navigate
          </span>
          <span className="seg">
            <span className="key">↵</span> open
          </span>
          <span className="seg">
            <span className="key">esc</span> close
          </span>
          <span style={{ flex: 1 }} />
          <span className="seg">
            {flat.length} result{flat.length === 1 ? "" : "s"}
          </span>
        </div>
      </div>
    </div>
  );
}

// Highlights the first case-insensitive occurrence of `term` inside
// `text`. Returns plain text when there's no match (cheap fall-back).
function highlight(text: string, term: string): React.ReactNode {
  const trimmed = term.trim();
  if (!trimmed || !text) return text;
  const idx = text.toLowerCase().indexOf(trimmed.toLowerCase());
  if (idx < 0) return text;
  return (
    <>
      {text.slice(0, idx)}
      <mark>{text.slice(idx, idx + trimmed.length)}</mark>
      {text.slice(idx + trimmed.length)}
    </>
  );
}
