import assert from "node:assert/strict";
import test from "node:test";

import {
  byRecentRun,
  byRecentWork,
  runDeepLinkFrom,
  runMatches,
} from "./src/searchRanking.ts";

const run = (over = {}) => ({
  id: "run-1",
  number: 1,
  work_slug: "WRK-001",
  work_name: "Auth rollout",
  goal: "ship the fallback",
  status: "running",
  updated_at: "2026-08-25T12:00:00+00:00",
  source_kind: null,
  source_ref: null,
  source_title: null,
  ...over,
});

test("runMatches searches goal, story and work", () => {
  const r = run({
    source_ref: "ST-04",
    source_title: "Password fallback during rollout",
  });
  for (const term of ["fallback", "ST-04", "st-04", "password", "WRK-001", "auth"]) {
    assert.equal(runMatches(r, term), true, term);
  }
  assert.equal(runMatches(r, "billing"), false);
});

test("runMatches treats an empty or blank term as everything", () => {
  assert.equal(runMatches(run(), ""), true);
  assert.equal(runMatches(run(), "   "), true);
});

test("runMatches tolerates a run with no story", () => {
  // source_ref / source_title are null for Loop-mode runs; matching
  // must not throw on them.
  assert.equal(runMatches(run(), "ship"), true);
  assert.equal(runMatches(run(), "ST-04"), false);
});

test("byRecentRun orders newest updated first", () => {
  const older = run({ id: "a", updated_at: "2026-08-24T09:00:00+00:00" });
  const newer = run({ id: "b", updated_at: "2026-08-25T09:00:00+00:00" });
  assert.deepEqual(
    [older, newer].sort(byRecentRun).map((r) => r.id),
    ["b", "a"],
  );
});

test("byRecentWork orders newest created first", () => {
  const works = [
    { slug: "WRK-001", created_at: "2026-08-01T00:00:00+00:00" },
    { slug: "WRK-002", created_at: "2026-08-20T00:00:00+00:00" },
  ];
  assert.deepEqual(
    [...works].sort(byRecentWork).map((w) => w.slug),
    ["WRK-002", "WRK-001"],
  );
});

test("runDeepLinkFrom reads a run id and its optional artifact", () => {
  assert.deepEqual(runDeepLinkFrom("?run=run-7"), {
    runId: "run-7",
    artifactId: null,
  });
  assert.deepEqual(runDeepLinkFrom("?run=run-7&artifact=ST-04"), {
    runId: "run-7",
    artifactId: "ST-04",
  });
});

test("runDeepLinkFrom returns null when there is no usable run", () => {
  // A stale bookmark must fall through to the work overview, never to
  // an error screen.
  for (const search of ["", "?chat=cht-1", "?run=", "?run=%20"]) {
    assert.equal(runDeepLinkFrom(search), null, JSON.stringify(search));
  }
});

test("runDeepLinkFrom ignores a blank artifact", () => {
  assert.deepEqual(runDeepLinkFrom("?run=run-7&artifact="), {
    runId: "run-7",
    artifactId: null,
  });
});
