import { useEffect, useState } from "react";

import { AgentTile } from "./AgentTile";
import { ShellTopbar } from "./ShellTopbar";
import { type AgentSummary, getAgent, getWork } from "./api";

export function AgentView({ agentSlug }: { agentSlug: string }) {
  const [agent, setAgent] = useState<AgentSummary | null>(null);
  const [readOnly, setReadOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAgent(agentSlug)
      .then(async (next) => {
        const work = await getWork(next.work_slug);
        if (cancelled) return;
        setAgent(next);
        setReadOnly(work.status !== "active");
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [agentSlug]);

  if (error) return <div className="work-loading form-error">{error}</div>;
  if (!agent) return <div className="work-loading hint">Loading...</div>;

  return (
    <div className="agent-page">
      <ShellTopbar
        crumbs={[
          { href: `/works/${agent.work_slug}`, label: agent.work_slug },
          { label: agentSlug },
        ]}
      />
      <main className="agent-page-body">
        <AgentTile
          agentSlug={agentSlug}
          agentName={agent.name}
          model={agent.model}
          provider={agent.provider}
          readOnly={readOnly}
          mode="page"
        />
      </main>
    </div>
  );
}
