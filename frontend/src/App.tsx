import { useEffect, useState } from "react";

import { AgentView } from "./AgentView";
import { Connections } from "./Connections";
import { Home } from "./Home";
import { useThemeStore } from "./state/theme";
import { useTweaksStore } from "./state/tweaks";
import { TweaksPanel } from "./TweaksPanel";
import { WorkView } from "./WorkView";

export function App() {
  const path = useRoute();
  useThemeAttribute();
  useAccentHueAttribute();

  return (
    <>
      <RouteView path={path} />
      <TweaksPanel />
    </>
  );
}

function RouteView({ path }: { path: string }) {
  if (path.startsWith("/agents/")) {
    const slug = path.slice("/agents/".length).split("/")[0];
    if (slug) return <AgentView agentSlug={slug} />;
  }
  if (path.startsWith("/works/")) {
    const slug = path.slice("/works/".length).split("/")[0];
    if (slug) return <WorkView workSlug={slug} />;
  }
  if (path === "/connections") {
    return <Connections />;
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

function useThemeAttribute(): void {
  const theme = useThemeStore((s) => s.theme);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
}

function useAccentHueAttribute(): void {
  const hue = useTweaksStore((s) => s.accentHue);
  useEffect(() => {
    document.documentElement.style.setProperty("--accent-h", String(hue));
  }, [hue]);
}
