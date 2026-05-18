import { useEffect, useState } from "react";

import { AgentView } from "./AgentView";
import { Home } from "./Home";
import { ProjectScreen } from "./ProjectScreen";
import { Settings, type SettingsSection } from "./Settings";
import { hydrateSettings, useSettingsStore } from "./state/settings";
import { TweaksPanel } from "./TweaksPanel";
import { UpdateBanner } from "./UpdateBanner";
import { WorkView } from "./WorkView";

export function App() {
  const path = useRoute();
  useSettingsHydration();
  useThemeAttribute();
  useAccentHueAttribute();

  return (
    <>
      <RouteView path={path} />
      <UpdateBanner />
      <TweaksPanel />
    </>
  );
}

function useSettingsHydration(): void {
  // Fetch the canonical settings row from the backend once on mount.
  // The store stays at defaults until hydration resolves — settings
  // changes mid-load are unlikely, and the alternative (localStorage
  // cache for fast-paint) would re-introduce the dual-source problem
  // the DB-backed store was meant to solve.
  useEffect(() => {
    void hydrateSettings();
  }, []);
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
  if (path.startsWith("/projects/")) {
    const slug = path.slice("/projects/".length).split("/")[0];
    if (slug) return <ProjectScreen projectSlug={slug} />;
  }
  if (path === "/connections") {
    // Legacy alias — Connections moved into Settings → Connections.
    // Use replace so the back button skips the redirect hop.
    window.location.replace("/settings/connections");
    return null;
  }
  if (path === "/settings" || path.startsWith("/settings/")) {
    const sub = path.slice("/settings".length).replace(/^\//, "");
    const section: SettingsSection =
      sub === "connections" || sub === "appearance" || sub === "about"
        ? sub
        : "tools";
    return <Settings section={section} />;
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
  const theme = useSettingsStore((s) => s.theme);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
}

function useAccentHueAttribute(): void {
  const hue = useSettingsStore((s) => s.accentHue);
  useEffect(() => {
    document.documentElement.style.setProperty("--accent-h", String(hue));
  }, [hue]);
}
