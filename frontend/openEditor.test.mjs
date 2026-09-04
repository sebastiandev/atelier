import assert from "node:assert/strict";
import test from "node:test";

import { openAgentInEditor, openRunInEditor } from "./src/api.ts";
import { openEditor } from "./src/openEditor.ts";
import { useSettingsStore } from "./src/state/settings.ts";

const AGENT = { kind: "agent", agentSlug: "agt-123", path: "/tmp/work" };
const RUN = {
  kind: "run",
  workSlug: "WRK-001",
  runId: "run-001",
  path: "/tmp/run-workspace",
};

function recorder() {
  const calls = [];
  return [
    calls,
    {
      openAgent: async (slug, editor) => calls.push(["agent", slug, editor]),
      openRun: async (work, run, editor) => calls.push(["run", work, run, editor]),
      navigate: (url) => calls.push(["url", url]),
    },
  ];
}

test("an editor without a URL handler is launched by the backend", async () => {
  const [calls, transport] = recorder();
  await openEditor("emacs", AGENT, transport);
  assert.deepEqual(calls, [["agent", "agt-123", "emacs"]]);
});

test("a run target uses the run endpoint, so it opens where console does", async () => {
  const [calls, transport] = recorder();
  await openEditor("emacs", RUN, transport);
  assert.deepEqual(calls, [["run", "WRK-001", "run-001", "emacs"]]);
});

test("the transport is chosen by descriptor, not by editor name", async () => {
  // An editor Atelier has never heard of still navigates as long as the
  // descriptor carries a template — nothing keys off "emacs".
  const [calls, transport] = recorder();
  const original = useSettingsStore.getState().editorOptions;
  useSettingsStore.setState({
    editorOptions: [
      { value: "someday", label: "Someday", command: "", url_template: "someday://{path}" },
      { value: "cli-only", label: "CLI only", command: "cli .", url_template: null },
    ],
  });
  try {
    await openEditor("someday", AGENT, transport);
    await openEditor("cli-only", AGENT, transport);
  } finally {
    // Restore directly: `reset()` would PUT to /api/settings, which has
    // no server here and leaks an unhandled rejection into other tests.
    useSettingsStore.setState({ editorOptions: original });
  }
  assert.deepEqual(calls, [
    ["url", "someday:///tmp/work"],
    ["agent", "agt-123", "cli-only"],
  ]);
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
    const [calls, transport] = recorder();
    await openEditor(
      editor,
      { kind: "agent", agentSlug: "agt-123", path: "/tmp/work folder" },
      transport,
    );
    assert.deepEqual(calls, [["url", expectedUrl]]);
  });
}

test("launch errors propagate to the calling surface", async () => {
  const failure = new Error("emacsclient unavailable");
  await assert.rejects(
    openEditor("emacs", AGENT, {
      openAgent: async () => { throw failure; },
      openRun: async () => assert.fail("must not use the run endpoint"),
      navigate: () => assert.fail("must not navigate"),
    }),
    failure,
  );
});

test("the API wrappers name the editor and surface the failure detail", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  try {
    globalThis.fetch = async (url, init) => {
      calls.push([url, init]);
      return new Response(null, { status: 204 });
    };
    await openAgentInEditor("agt-123", "emacs");
    await openRunInEditor("WRK-001", "run-001", "emacs");
    assert.deepEqual(calls, [
      ["/api/agents/agt-123/open-in-editor?editor=emacs", { method: "POST" }],
      [
        "/api/works/WRK-001/runs/run-001/open-in-editor?editor=emacs",
        { method: "POST" },
      ],
    ]);

    globalThis.fetch = async () => new Response(
      JSON.stringify({ detail: "open in editor failed: emacsclient is not on PATH" }),
      { status: 500, statusText: "Internal Server Error" },
    );
    await assert.rejects(
      openAgentInEditor("agt-123", "emacs"),
      /emacsclient is not on PATH/,
    );
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
