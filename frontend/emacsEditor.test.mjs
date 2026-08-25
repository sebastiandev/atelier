import assert from "node:assert/strict";
import test from "node:test";

import { openAgentInEditor } from "./src/api.ts";
import { openAgentEditor } from "./src/emacsEditor.ts";
import { useSettingsStore } from "./src/state/settings.ts";

test("Emacs uses the backend transport and does not navigate", async () => {
  const calls = [];
  await openAgentEditor("emacs", "agt-123", "/tmp/work", {
    openEmacs: async (slug) => calls.push(["http", slug]),
    navigate: (url) => calls.push(["url", url]),
  });
  assert.deepEqual(calls, [["http", "agt-123"]]);
});

const URL_EDITOR_CASES = [
  ["vscode", "vscode://file/tmp/work%20folder"],
  ["cursor", "cursor://file/tmp/work%20folder"],
  ["zed", "zed://file/tmp/work%20folder"],
  ["pycharm", "pycharm://open?file=%2Ftmp%2Fwork%20folder"],
  ["idea", "idea://open?file=%2Ftmp%2Fwork%20folder"],
  ["webstorm", "webstorm://open?file=%2Ftmp%2Fwork%20folder"],
  ["vim", "mvim://open?url=file:///tmp/work%20folder"],
];

for (const [editor, expectedUrl] of URL_EDITOR_CASES) {
  test(`${editor} retains URL navigation`, async () => {
    const calls = [];
    await openAgentEditor(editor, "agt-123", "/tmp/work folder", {
      openEmacs: async (slug) => calls.push(["http", slug]),
      navigate: (url) => calls.push(["url", url]),
    });
    assert.deepEqual(calls, [["url", expectedUrl]]);
  });
}

test("Emacs launch errors propagate to the calling surface", async () => {
  const failure = new Error("emacsclient unavailable");
  await assert.rejects(
    openAgentEditor("emacs", "agt-123", "/tmp/work", {
      openEmacs: async () => { throw failure; },
      navigate: () => assert.fail("must not navigate"),
    }),
    failure,
  );
});

test("the API wrapper posts to the agent-only endpoint and surfaces detail", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  try {
    globalThis.fetch = async (url, init) => {
      calls.push([url, init]);
      return new Response(null, { status: 204 });
    };
    await openAgentInEditor("agt-123");
    assert.deepEqual(calls, [[
      "/api/agents/agt-123/open-in-editor",
      { method: "POST" },
    ]]);

    globalThis.fetch = async () => new Response(
      JSON.stringify({ detail: "emacsclient must be on PATH" }),
      { status: 500, statusText: "Internal Server Error" },
    );
    await assert.rejects(openAgentInEditor("agt-123"), /emacsclient must be on PATH/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("the pre-hydration fallback catalog includes Emacs without changing default", () => {
  const state = useSettingsStore.getState();
  assert.equal(state.editor, "vscode");
  assert.deepEqual(state.editorOptions.at(-1), {
    value: "emacs",
    label: "Emacs",
    command: "emacsclient -n -c .",
    url_template: null,
  });
});
