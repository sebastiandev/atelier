import assert from "node:assert/strict";
import test from "node:test";

import { filterSessionCommands } from "./src/pickerSearch.ts";
import {
  activeCommandMention,
  latestSessionCommands,
  unknownCommandName,
} from "./src/sessionCommands.ts";

const COMMANDS = [
  { name: "init", description: "guided AGENTS.md setup", hint: null, origin: null },
  {
    name: "review",
    description: "review changes",
    hint: "commit|branch",
    origin: null,
  },
];

test("latestSessionCommands takes the newest advertised set", () => {
  const events = [
    { seq: 1, type: "session_commands", ts: "", commands: [{ name: "old" }] },
    { seq: 2, type: "message_delta", ts: "" },
    {
      seq: 3,
      type: "session_commands",
      ts: "",
      commands: [{ name: "init", description: "guided", hint: "topic" }],
    },
  ];
  assert.deepEqual(latestSessionCommands(events), [
    { name: "init", description: "guided", hint: "topic", origin: null },
  ]);
});

test("latestSessionCommands is empty for a session that advertises nothing", () => {
  assert.deepEqual(latestSessionCommands([{ seq: 1, type: "message_delta" }]), []);
});

test("latestSessionCommands drops entries without a name", () => {
  const events = [
    {
      seq: 1,
      type: "session_commands",
      ts: "",
      commands: [{ description: "nameless" }, { name: "ok" }, null],
    },
  ];
  assert.deepEqual(
    latestSessionCommands(events).map((c) => c.name),
    ["ok"],
  );
});

test("activeCommandMention only fires on a leading slash", () => {
  for (const [text, cursor, expected] of [
    ["/", 1, ""],
    ["/rev", 4, "rev"],
    ["/review ", 8, null],
    ["/review foo", 11, null],
    ["look at src/foo", 15, null],
    ["and/or", 6, null],
    ["hello", 5, null],
    ["", 0, null],
  ]) {
    const state = activeCommandMention(text, cursor);
    assert.equal(
      state === null ? null : state.query,
      expected,
      `${JSON.stringify(text)} @${cursor}`,
    );
  }
});

test("activeCommandMention anchors at position 0", () => {
  assert.deepEqual(activeCommandMention("/init", 5), {
    start: 0,
    end: 5,
    query: "init",
    index: 0,
  });
});


test("unknownCommandName rejects a name the agent never advertised", () => {
  assert.equal(unknownCommandName("/nope now", COMMANDS), "nope");
  assert.equal(unknownCommandName("/init", COMMANDS), null);
  assert.equal(unknownCommandName("/review main", COMMANDS), null);
  assert.equal(unknownCommandName("just a message", COMMANDS), null);
});

test("unknownCommandName stays silent when nothing is advertised", () => {
  // A provider without command support must keep sending slash text
  // verbatim rather than having the composer refuse it.
  assert.equal(unknownCommandName("/anything", []), null);
});

test("a draft that is just a path is not a command", () => {
  // Absolute paths and URLs start with a slash too; refusing to send
  // them would be worse than the silent no-op the guard exists for.
  for (const text of ["/Users/me/notes.md", "/etc/hosts", "/api/v1/works"]) {
    assert.equal(unknownCommandName(text, COMMANDS), null, text);
    assert.equal(activeCommandMention(text, text.length), null, text);
  }
});

test("the picker still opens on a bare slash and a partial name", () => {
  assert.equal(activeCommandMention("/", 1)?.query, "");
  assert.equal(activeCommandMention("/re", 3)?.query, "re");
});




test("filterSessionCommands requires every term to hit", () => {
  // Spec §03: terms split on / - and whitespace, all must match, across
  // name and description together.
  const commands = [
    { name: "flaky-tests", description: "fix intermittent CI tests", hint: null, origin: null },
    { name: "e2e-local-testing", description: "smoke tests", hint: null, origin: null },
  ];
  assert.deepEqual(
    filterSessionCommands(commands, "flaky").map((c) => c.name),
    ["flaky-tests"],
  );
  // "test" hits both; adding "ci" narrows to the one whose description has it
  assert.deepEqual(
    filterSessionCommands(commands, "test-ci").map((c) => c.name),
    ["flaky-tests"],
  );
  assert.equal(filterSessionCommands(commands, "flaky zzz").length, 0);
});

test("latestSessionCommands reads an origin when the agent reports one", () => {
  const events = [
    {
      seq: 1,
      type: "session_commands",
      ts: "",
      commands: [
        { name: "a", description: "", origin: "project" },
        { name: "b", description: "", origin: "nonsense" },
      ],
    },
  ];
  assert.deepEqual(
    latestSessionCommands(events).map((c) => c.origin),
    ["project", null],
  );
});
