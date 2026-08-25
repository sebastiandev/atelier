import { useEffect, useMemo, useRef, useState } from "react";

import { listRecentRuns, type ProjectSummary, type RecentRun, type WorkSummary } from "./api";
import { CheckIcon, SearchIcon } from "./Icons";
import {
  byRecentRun,
  byRecentWork,
  runMatches,
} from "./searchRanking";

export type SearchScope = "all" | { slug: string };

type Result =
  | { kind: "project"; project: ProjectSummary }
  | { kind: "work"; work: WorkSummary; project: ProjectSummary | null }
  | { kind: "run"; run: RecentRun };

//: Alongside the existing 8 projects / 20 works. A picker, not a report.
const RUN_LIMIT = 12;

//: Terminal run statuses, mirroring the set `LoopRunRepository.list_active`
//: excludes. Defined as "finished" rather than "running" so a new
//: in-flight status shows a live dot by default instead of a wrong tick.
const FINISHED_RUN_STATUSES = new Set([
  "accepted",
  "cancelled",
  "cleaned",
  "failed",
]);

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
  const [runs, setRuns] = useState<RecentRun[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const rowRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Fetched here rather than passed in: the modal has four mount points
  // (App, Home, ProjectScreen, WorkView) and prop-drilling would touch
  // them all. Failure is silent by design — projects and works must
  // render whether or not this resolves.
  useEffect(() => {
    let cancelled = false;
    listRecentRuns()
      .then((rows) => {
        if (!cancelled) setRuns(rows);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const projectMap = useMemo(() => {
    const m = new Map<string, ProjectSummary>();
    for (const p of projects) m.set(p.slug, p);
    return m;
  }, [projects]);

  const workBySlug = useMemo(() => {
    const m = new Map<string, WorkSummary>();
    for (const w of works) m.set(w.slug, w);
    return m;
  }, [works]);

  const results = useMemo<{
    projects: ProjectSummary[];
    works: WorkSummary[];
    runs: RecentRun[];
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
    // A run belongs to its work's project, so the scope toggle reaches
    // it through the work rather than through anything on the run.
    const runList = runs.filter((run) => {
      if (!inScope(workBySlug.get(run.work_slug)?.project_slug)) return false;
      return runMatches(run, term);
    });
    return {
      projects: projList.slice(0, 8),
      works: [...workList].sort(byRecentWork).slice(0, 20),
      runs: [...runList].sort(byRecentRun).slice(0, RUN_LIMIT),
    };
  }, [q, scope, works, projects, runs, workBySlug]);

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
      ...results.runs.map<Result>((run) => ({ kind: "run", run })),
    ],
    [results, projectMap],
  );

  useEffect(() => {
    setActive(0);
  }, [q, scope]);

  // ↑/↓ move a cursor that can leave the viewport: the results panel
  // scrolls, so the active row has to be pulled back into it. Without
  // this the list only follows the mouse.
  useEffect(() => {
    rowRefs.current.length = flat.length;
    rowRefs.current[active]?.scrollIntoView({ block: "nearest" });
  }, [active, flat.length]);

  function openResult(r: Result) {
    if (r.kind === "project") {
      window.location.assign(`/projects/${r.project.slug}`);
    } else if (r.kind === "run") {
      // The story ref rides along so a planning run lands on its run
      // view directly, with no pass through the work overview.
      const artifact = r.run.source_ref
        ? `&artifact=${encodeURIComponent(r.run.source_ref)}`
        : "";
      window.location.assign(
        `/works/${r.run.work_slug}?run=${encodeURIComponent(r.run.id)}${artifact}`,
      );
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
      <div className="search-modal" role="dialog" aria-modal="true" aria-label="Search">
        <div className="search-input-row">
          <span className="ico">
            <SearchIcon size={14} />
          </span>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="search work units, projects, loop runs, ids…"
          />
          {scopeProject && (
            <div className="scope-toggle view-toggle" role="tablist">
              <button
                className={`view-toggle-btn${scope === "all" ? " active" : ""}`}
                onClick={() => setScope("all")}
              >
                all
              </button>
              <button
                className={`view-toggle-btn${scope !== "all" ? " active" : ""}`}
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
                  ref={(node) => {
                    rowRefs.current[i] = node;
                  }}
                  className={
                    "search-result" + (active === i ? " kbd-active" : "")
                  }
                  style={{
                    ["--proj-h" as string]: String(p.color),
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
                    ref={(node) => {
                      rowRefs.current[flatIdx] = node;
                    }}
                    className={
                      "search-result" +
                      (active === flatIdx ? " kbd-active" : "")
                    }
                    style={
                      p
                        ? {
                            ["--proj-h" as string]: String(p.color),
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

          {results.runs.length > 0 && (
            <>
              <div className="search-group-hd">
                <span>Loop runs</span>
                <span className="count">{results.runs.length}</span>
              </div>
              {results.runs.map((run, i) => {
                const flatIdx =
                  results.projects.length + results.works.length + i;
                const work = workBySlug.get(run.work_slug) ?? null;
                const p = work?.project_slug
                  ? projectMap.get(work.project_slug) ?? null
                  : null;
                // A story-triggered run's `goal` is its artifact path,
                // not prose — the story title is the readable label, and
                // the ref goes underneath. A Loop-mode run has neither,
                // so its goal *is* the label and status fills the line.
                const label = run.source_title ?? run.goal;
                const sub = run.source_ref ?? run.status;
                return (
                  <button
                    key={`${run.work_slug}:${run.id}`}
                    ref={(node) => {
                      rowRefs.current[flatIdx] = node;
                    }}
                    className={
                      "search-result" +
                      (active === flatIdx ? " kbd-active" : "")
                    }
                    style={
                      p ? { ["--proj-h" as string]: String(p.color) } : undefined
                    }
                    onMouseEnter={() => setActive(flatIdx)}
                    onClick={() => openResult({ kind: "run", run })}
                  >
                    <span
                      className="pip"
                      style={
                        !p
                          ? { background: "var(--bg-2)", color: "var(--fg-3)" }
                          : undefined
                      }
                    >
                      {FINISHED_RUN_STATUSES.has(run.status) ? (
                        <CheckIcon size={9} />
                      ) : (
                        <span className="dot live" />
                      )}
                    </span>
                    <span className="id">
                      {run.number > 0 ? `run ${run.number}` : "run"}
                    </span>
                    <span className="body">
                      <div className="name">{highlight(label, q)}</div>
                      <div className="desc">{highlight(sub, q)}</div>
                    </span>
                    <span className="tail">{highlight(run.work_name, q)}</span>
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
