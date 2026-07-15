import assert from "node:assert/strict";
import test from "node:test";

import { deriveProviderAuthRequirement } from "./src/providerAuth.ts";

const event = (seq, type, fields = {}) => ({
  seq,
  type,
  ts: "2026-07-15T00:00:00Z",
  ...fields,
});

const authError = (seq) =>
  event(seq, "error", {
    message: "Authentication required",
    code: "authentication_required",
    recovery_command: "claude auth login",
  });

test("derives only enriched provider authentication errors", () => {
  assert.equal(
    deriveProviderAuthRequirement([
      event(1, "error", { message: "Authentication required" }),
    ]),
    null,
  );
  assert.deepEqual(deriveProviderAuthRequirement([authError(2)]), {
    code: "authentication_required",
    recoveryCommand: "claude auth login",
    seq: 2,
  });
});

test("later provider output clears a historical authentication error", () => {
  assert.deepEqual(
    deriveProviderAuthRequirement([
      authError(1),
      event(2, "status_change", { status: "thinking" }),
    ])?.seq,
    1,
  );
  assert.equal(
    deriveProviderAuthRequirement([
      authError(1),
      event(2, "message_delta", { text: "Recovered" }),
    ]),
    null,
  );
});

test("confirmation hides one failure and a later failure reopens the gate", () => {
  assert.equal(deriveProviderAuthRequirement([authError(3)], 3), null);
  assert.equal(
    deriveProviderAuthRequirement([authError(3), authError(7)], 3)?.seq,
    7,
  );
});
