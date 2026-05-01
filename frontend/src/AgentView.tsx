import { AgentTile } from "./AgentTile";

export function AgentView({ agentSlug }: { agentSlug: string }) {
  return <AgentTile agentSlug={agentSlug} />;
}
