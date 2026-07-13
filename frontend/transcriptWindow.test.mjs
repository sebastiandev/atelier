import assert from "node:assert/strict";
import test from "node:test";

import {
  adaptiveTranscriptLimit,
  transcriptWindowFor,
} from "./src/transcriptWindow.ts";

test("adaptiveTranscriptLimit contracts as the session grows", () => {
  for (const [seq, expected] of [
    [1_000, 500],
    [1_001, 250],
    [5_001, 100],
    [10_001, 50],
  ]) {
    assert.equal(adaptiveTranscriptLimit(seq), expected);
  }
});

test("transcriptWindowFor preserves an active delta prefix", () => {
  const event = (seq, type) => ({ seq, type });
  const events = [
    event(1, "user_input"),
    event(2, "message_delta"),
    event(3, "message_delta"),
    event(4, "message_delta"),
    event(5, "message_delta"),
  ];

  const window = transcriptWindowFor(events, 2);

  assert.deepEqual(window.events.map(({ seq }) => seq), [2, 3, 4, 5]);
  assert.equal(window.hiddenCount, 1);
});

test("transcriptWindowFor does not expand a completed delta run", () => {
  const events = [
    { seq: 1, type: "user_input" },
    { seq: 2, type: "message_delta" },
    { seq: 3, type: "message_delta" },
    { seq: 4, type: "message_delta" },
    { seq: 5, type: "message_delta" },
    { seq: 6, type: "message_complete" },
  ];

  const window = transcriptWindowFor(events, 3);

  assert.deepEqual(window.events.map(({ seq }) => seq), [4, 5, 6]);
});
