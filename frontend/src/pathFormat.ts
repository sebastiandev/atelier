export function shortenPath(p: string): string {
  if (!p) return "";
  // Substitute ``$HOME/...`` -> ``~/...`` for display when we can detect
  // home from the path. Backend sends absolute paths; FE has no env var
  // access, so we just probe common macOS/Linux prefixes.
  const homeMatch = p.match(/^(\/Users\/[^/]+|\/home\/[^/]+)\/(.*)$/);
  const display = homeMatch ? `~/${homeMatch[2]}` : p;
  const parts = display.split("/");
  if (parts.length <= 3) return display;
  return [parts[0], "...", ...parts.slice(-2)].join("/");
}
