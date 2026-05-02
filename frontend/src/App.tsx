import { useEffect, useState } from "react";

import { AgentView } from "./AgentView";
import { Home } from "./Home";
import { WorkView } from "./WorkView";

export function App() {
  const path = useRoute();

  if (path.startsWith("/agents/")) {
    const slug = path.slice("/agents/".length).split("/")[0];
    if (slug) return <AgentView agentSlug={slug} />;
  }

  if (path.startsWith("/works/")) {
    const slug = path.slice("/works/".length).split("/")[0];
    if (slug) return <WorkView workSlug={slug} />;
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
