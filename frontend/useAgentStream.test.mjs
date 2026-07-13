import assert from "node:assert/strict";
import test from "node:test";

import { mergeEvents } from "./src/mergeEvents.ts";

test("mergeEvents appends live events and merges older history", () => {
  const event = (seq, text = String(seq)) => ({
    seq,
    text,
    ts: "2026-07-13T00:00:00Z",
    type: "message_delta",
  });

  assert.deepEqual(
    mergeEvents([event(1)], [event(2), event(3)]).map(({ seq }) => seq),
    [1, 2, 3],
  );

  const replacement = event(2, "replacement");
  const merged = mergeEvents(
    [event(1), event(2), event(3)],
    [event(0), replacement],
  );
  assert.deepEqual(merged.map(({ seq }) => seq), [0, 1, 2, 3]);
  assert.equal(merged[2], replacement);
});
