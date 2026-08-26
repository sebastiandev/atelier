/**
 * Open a workspace in the user's editor.
 *
 * Two transports, chosen by what the backend says about the editor —
 * never by its name. An editor with a `url_template` is opened by the
 * OS from a URL, exactly as it always has been; one without (Emacs
 * today) has no scheme to hand the browser, so Atelier starts it
 * server-side through the same resolve-and-launch path that reveal and
 * open-in-console use.
 *
 * Callers pass whichever entity owns the workspace — a run when it has
 * one of its own, otherwise the agent — mirroring how the backend
 * resolves the directory. That keeps a command-launched editor landing
 * exactly where a URL-handler one does.
 */
// Explicit .ts extensions: this module is unit-tested under
// `node --test`, whose resolver does not fill them in.
import { openAgentInEditor, openRunInEditor } from "./api.ts";
import { editorUrl, opensFromUrl, type EditorChoice } from "./state/settings.ts";

export type EditorTarget =
  | { kind: "agent"; agentSlug: string; path: string }
  | { kind: "run"; workSlug: string; runId: string; path: string };

type EditorTransport = {
  openAgent: (agentSlug: string, editor: EditorChoice) => Promise<void>;
  openRun: (
    workSlug: string,
    runId: string,
    editor: EditorChoice,
  ) => Promise<void>;
  navigate: (url: string) => void;
};

const defaultTransport: EditorTransport = {
  openAgent: openAgentInEditor,
  openRun: openRunInEditor,
  navigate: (url) => {
    window.location.href = url;
  },
};

export async function openEditor(
  editor: EditorChoice,
  target: EditorTarget,
  transport: EditorTransport = defaultTransport,
): Promise<void> {
  if (opensFromUrl(editor)) {
    transport.navigate(editorUrl(editor, target.path));
    return;
  }
  if (target.kind === "run") {
    await transport.openRun(target.workSlug, target.runId, editor);
    return;
  }
  await transport.openAgent(target.agentSlug, editor);
}
