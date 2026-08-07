import assert from "node:assert/strict";
import test from "node:test";

import { overrideDelta } from "./src/planningSetup.ts";

const config = (provider, model, options = {}) => ({ provider, model, options });

test("a picked provider and model are recorded", () => {
  const delta = overrideDelta(
    config("opencode", "anthropic/claude-sonnet-5"),
    config("opencode", "openai/gpt-5.6-terra"),
  );

  assert.equal(delta.provider, "opencode");
  assert.equal(delta.model, "anthropic/claude-sonnet-5");
});

test("a pick matching the inherited config is still recorded, not collapsed to inherit", () => {
  // Otherwise the stage silently follows the entry stage's next model change.
  const inherited = config("opencode", "openai/gpt-5.6-terra");

  const delta = overrideDelta(config("opencode", "openai/gpt-5.6-terra"), inherited);

  assert.equal(delta.provider, "opencode");
  assert.equal(delta.model, "openai/gpt-5.6-terra");
});

test("options stay sparse: only what differs from the inherited config is stored", () => {
  const delta = overrideDelta(
    config("opencode", "m", { reasoning_effort: "high", mode: "build" }),
    config("opencode", "m", { reasoning_effort: "medium", mode: "build" }),
  );

  assert.deepEqual(delta.options, { reasoning_effort: "high" });
});

test("every option is stored when there is nothing to inherit from", () => {
  const delta = overrideDelta(config("opencode", "m", { mode: "plan" }), null);

  assert.deepEqual(delta.options, { mode: "plan" });
});
