import { useState } from "react";

type Props = {
  prefixes: string[];
  onChange: (prefixes: string[]) => void;
  compact?: boolean;
};

/** Edit an ordered set of single-command approval prefixes. */
export function CommandPrefixEditor({ prefixes, onChange, compact = false }: Props) {
  const [draft, setDraft] = useState("");

  function add() {
    const prefix = draft.trim();
    if (!prefix || prefixes.includes(prefix)) return;
    onChange([...prefixes, prefix]);
    setDraft("");
  }

  return (
    <div className={`command-prefix-editor${compact ? " compact" : ""}`}>
      {compact && <form onSubmit={(event) => { event.preventDefault(); add(); }}>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value.replace(/[\r\n]/g, ""))}
          placeholder="e.g. git status"
          aria-label="Approved command prefix"
        />
        <button type="submit" disabled={!draft.trim() || prefixes.includes(draft.trim())}>Add</button>
      </form>}
      {prefixes.map((prefix) => (
        <div key={prefix}>
          <code title={prefix}>{compact && "$ "}{prefix}{compact && " *"}</code>
          <button type="button" onClick={() => onChange(prefixes.filter((item) => item !== prefix))} aria-label={`Remove ${prefix}`}>×</button>
        </div>
      ))}
      {!prefixes.length && <span>No commands are approved automatically.</span>}
      {!compact && <form onSubmit={(event) => { event.preventDefault(); add(); }}>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value.replace(/[\r\n]/g, ""))}
          placeholder="e.g. dt sh -s app-endpoints"
          aria-label="Approved command prefix"
        />
        <button type="submit" disabled={!draft.trim() || prefixes.includes(draft.trim())}>Add</button>
      </form>}
    </div>
  );
}
