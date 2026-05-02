import { useEffect, useState } from "react";

import { AgentView } from "./AgentView";

export function App() {
  const path = useRoute();

  if (path.startsWith("/agents/")) {
    const slug = path.slice("/agents/".length).split("/")[0];
    if (slug) return <AgentView agentSlug={slug} />;
  }

  return <Home />;
}

function useRoute(): string {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const handler = () => setPath(window.location.pathname);
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);
  return path;
}

function Home() {
  return (
    <div className="home">
      <h1>Atelier</h1>
      <p>Walking-skeleton dev mode. Backend at <code>localhost:8001</code>.</p>
      <p>
        Create a Work + Agent via the API, then visit{" "}
        <a href="/agents/agt-1">/agents/agt-1</a>:
      </p>
      <pre>{`curl -s -X POST /api/works \\
  -H 'Content-Type: application/json' \\
  -d '{"name":"Demo","description":"d","folder":"/tmp/demo"}'

# Stub-backed Amp agent (no API key required):
curl -s -X POST /api/works/WRK-001/agents \\
  -H 'Content-Type: application/json' \\
  -d '{"name":"Builder","persona":"developer","role":"build","provider":"amp","model":"smart"}'

# Real Claude agent (requires ANTHROPIC_API_KEY in .env.local + Claude Code CLI):
# Haiku is the cheapest option — use it for smoke-testing.
curl -s -X POST /api/works/WRK-001/agents \\
  -H 'Content-Type: application/json' \\
  -d '{"name":"Architect","persona":"architect","role":"design",
       "provider":"claude-code","model":"claude-haiku-4-5"}'`}</pre>
      <p>
        Set <code>STUB_EVENT_DELAY=0.4</code> on the backend to see events
        stream visibly rather than landing all at once.
      </p>
    </div>
  );
}
