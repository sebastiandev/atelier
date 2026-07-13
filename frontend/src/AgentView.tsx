import { AgentTile } from "./AgentTile";
import { ShellTopbar } from "./ShellTopbar";

export function AgentView({ agentSlug }: { agentSlug: string }) {
  return (
    <div className="agent-page">
      <ShellTopbar crumbs={[{ label: agentSlug }]} />
      <main className="agent-page-body">
        <AgentTile agentSlug={agentSlug} mode="page" />
      </main>
    </div>
  );
}
