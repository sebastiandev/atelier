import { openAgentInEditor } from "./api.ts";
import { editorUrl, type EditorChoice } from "./state/settings.ts";

type EditorTransport = {
  openEmacs: (agentSlug: string) => Promise<void>;
  navigate: (url: string) => void;
};

const defaultTransport: EditorTransport = {
  openEmacs: openAgentInEditor,
  navigate: (url) => {
    window.location.href = url;
  },
};

/** Select the HTTP Emacs transport or the historical URL-handler transport. */
export async function openAgentEditor(
  editor: EditorChoice,
  agentSlug: string,
  workspacePath: string,
  transport: EditorTransport = defaultTransport,
): Promise<void> {
  if (editor === "emacs") {
    await transport.openEmacs(agentSlug);
    return;
  }
  transport.navigate(editorUrl(editor, workspacePath));
}
