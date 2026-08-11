import assert from "node:assert/strict";
import test from "node:test";

import {
  completedEditPaths,
  editedPathsInCall,
  relativeToRoot,
  resolvePlanLink,
} from "./src/editedPaths.ts";

function call(id, name, args, extra = {}) {
  return { seq: 1, type: "tool_call", ts: "", tool_id: id, name, arguments: args, ...extra };
}

function result(id, extra = {}) {
  return { seq: 2, type: "tool_result", ts: "", tool_id: id, ...extra };
}

test("every canonical write tool reports its path", () => {
  for (const name of ["Edit", "MultiEdit", "Write", "NotebookEdit"]) {
    assert.deepEqual(
      editedPathsInCall(call("t", name, { path: "/plan/stories/story-002.md" })),
      ["/plan/stories/story-002.md"],
      name,
    );
  }
});

test("a read is not an edit", () => {
  assert.deepEqual(editedPathsInCall(call("t", "Read", { path: "/plan/intent.md" })), []);
  assert.deepEqual(editedPathsInCall(call("t", "Grep", { pattern: "x" })), []);
});

test("an ACP edit-kind tool counts even without a canonical name", () => {
  const event = call("t", "apply_patch", {}, {
    kind: "edit",
    locations: [{ path: "/plan/intent.md" }, { path: "/plan/stories/story-002.md", line: 4 }],
  });

  assert.deepEqual(editedPathsInCall(event), [
    "/plan/intent.md",
    "/plan/stories/story-002.md",
  ]);
});

test("a path named twice is reported once", () => {
  const event = call("t", "Edit", { path: "/plan/intent.md" }, {
    locations: [{ path: "/plan/intent.md" }],
  });

  assert.deepEqual(editedPathsInCall(event), ["/plan/intent.md"]);
});

test("a write counts only once the tool has returned", () => {
  const pending = [call("t1", "Write", { path: "/plan/intent.md" })];

  // The bytes are not on disk until the tool returns; reacting to the call
  // reads the old file.
  assert.deepEqual(completedEditPaths(pending).paths, []);
  assert.deepEqual(
    completedEditPaths([...pending, result("t1")]).paths,
    ["/plan/intent.md"],
  );
});

test("a result arriving in a later batch than its call still names the path", () => {
  // The live shape. Filtering the event list by the cursor before scanning it
  // orphans the result and the caller silently sees nothing.
  const events = [
    { ...call("t1", "Write", { path: "/plan/intent.md" }), seq: 10 },
    { ...result("t1"), seq: 11 },
  ];

  const first = completedEditPaths(events, 0);
  assert.deepEqual(first.paths, ["/plan/intent.md"]);
  assert.equal(first.lastSeq, 11);

  // Replayed on the next render with the cursor advanced: not re-announced.
  assert.deepEqual(completedEditPaths(events, first.lastSeq).paths, []);
});

test("replayed history is not re-announced after a reconnect", () => {
  const events = [
    { ...call("t1", "Edit", { path: "/plan/a.md" }), seq: 1 },
    { ...result("t1"), seq: 2 },
    { ...call("t2", "Edit", { path: "/plan/b.md" }), seq: 3 },
    { ...result("t2"), seq: 4 },
  ];

  assert.deepEqual(completedEditPaths(events, 2).paths, ["/plan/b.md"]);
});

test("a failed write is not a change", () => {
  const events = [
    call("t1", "Write", { path: "/plan/intent.md" }),
    result("t1", { ok: false }),
    call("t2", "Edit", { path: "/plan/other.md" }),
    result("t2", { error: "permission denied" }),
  ];

  assert.deepEqual(completedEditPaths(events).paths, []);
});

test("a result whose call scrolled out of the window is ignored", () => {
  assert.deepEqual(completedEditPaths([result("t-gone")]).paths, []);
});

test("relativeToRoot strips the plan root and rejects anything outside it", () => {
  const root = "/Users/seba/work/plan";

  assert.equal(relativeToRoot("/Users/seba/work/plan/stories/s.md", root), "stories/s.md");
  assert.equal(relativeToRoot("/Users/seba/work/plan/", root), null);
  assert.equal(relativeToRoot("/Users/seba/work/other/s.md", root), null);
  // A sibling directory sharing the root's prefix is not inside it.
  assert.equal(relativeToRoot("/Users/seba/work/plan-old/s.md", root), null);
  assert.equal(relativeToRoot("/Users/seba/work/plan/s.md", `${root}/`), "s.md");
});

test("resolvePlanLink resolves relative links against the linking document", () => {
  const from = "stories/story-001.md";

  assert.equal(resolvePlanLink("story-002.md", from), "stories/story-002.md");
  assert.equal(resolvePlanLink("./story-002.md", from), "stories/story-002.md");
  assert.equal(resolvePlanLink("../intent.md", from), "intent.md");
  assert.equal(resolvePlanLink("/intent.md", from), "intent.md");
  assert.equal(resolvePlanLink("../bugs/bug-001.md", from), "bugs/bug-001.md");
});

test("resolvePlanLink drops a fragment but keeps the document", () => {
  assert.equal(
    resolvePlanLink("../intent.md#goals", "stories/story-001.md"),
    "intent.md",
  );
});

test("resolvePlanLink leaves anything that is not a plan document alone", () => {
  const from = "stories/story-001.md";

  for (const href of [
    "https://github.com/acme/repo",
    "http://example.com",
    "mailto:someone@example.com",
    "//cdn.example.com/x.md",
    "#a-heading-in-this-doc",
    "",
    "   ",
  ]) {
    assert.equal(resolvePlanLink(href, from), null, href);
  }
});

test("resolvePlanLink refuses to climb above the plan root", () => {
  assert.equal(resolvePlanLink("../../secrets.md", "stories/story-001.md"), null);
});

test("REAL ACP SHAPE: the path is on the result, not the call", () => {
  // Taken from a live planning chat. The call names no file at all -- the
  // provider reports it only on completion, as `diff.path`. Reading the call
  // alone means the edit is never seen.
  const events = [
    {
      seq: 5390,
      type: "tool_call",
      ts: "",
      tool_id: "call_jpIg",
      name: "Editing files",
      arguments: {},
      kind: "edit",
      title: "Editing files",
    },
    {
      seq: 5391,
      type: "tool_result",
      ts: "",
      tool_id: "call_jpIg",
      is_error: false,
      diff: { path: "/repo/bmad/port_lpn/stories/01-migrate.md", old_text: "a", new_text: "b" },
    },
  ];

  const { paths } = completedEditPaths(events);
  assert.deepEqual(paths, ["/repo/bmad/port_lpn/stories/01-migrate.md"]);
  assert.equal(
    relativeToRoot(paths[0], "/repo/bmad/port_lpn"),
    "stories/01-migrate.md",
  );
});

test("a failed edit result is ignored whichever error flag the provider sets", () => {
  const failed = (extra) => [{
    seq: 2, type: "tool_result", ts: "", tool_id: "t",
    diff: { path: "/repo/x.md" }, ...extra,
  }];

  assert.deepEqual(completedEditPaths(failed({ is_error: true })).paths, []);
  assert.deepEqual(completedEditPaths(failed({ ok: false })).paths, []);
  assert.deepEqual(completedEditPaths(failed({ error: "boom" })).paths, []);
  assert.deepEqual(completedEditPaths(failed({ is_error: false })).paths, ["/repo/x.md"]);
});
