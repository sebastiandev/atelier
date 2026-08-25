import assert from "node:assert/strict";
import test from "node:test";

import { matchesAllTerms, searchTerms, splitMatches } from "./src/pickerSearch.ts";

test("searchTerms splits on slash, dash and whitespace", () => {
  assert.deepEqual(searchTerms("flaky-tests"), ["flaky", "tests"]);
  assert.deepEqual(searchTerms("epics/epic-1 st"), ["epics", "epic", "1", "st"]);
  assert.deepEqual(searchTerms("   "), []);
});

test("matchesAllTerms requires every term", () => {
  assert.equal(matchesAllTerms("flaky tests in CI", ["flaky", "ci"]), true);
  assert.equal(matchesAllTerms("flaky tests in CI", ["flaky", "nope"]), false);
});

test("splitMatches marks matched runs and leaves the rest alone", () => {
  assert.deepEqual(splitMatches("flaky-tests", ["test"]), [
    { text: "flaky-", marked: false },
    { text: "test", marked: true },
    { text: "s", marked: false },
  ]);
});

test("splitMatches collapses overlapping hits into one run", () => {
  assert.deepEqual(splitMatches("testing", ["test", "esti"]), [
    { text: "testi", marked: true },
    { text: "ng", marked: false },
  ]);
});

test("splitMatches returns one unmarked segment when there is nothing to mark", () => {
  assert.deepEqual(splitMatches("plain", []), [{ text: "plain", marked: false }]);
});
