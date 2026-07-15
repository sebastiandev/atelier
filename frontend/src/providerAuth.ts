export const PROVIDER_AUTH_REQUIRED_CODE = "authentication_required";

export type ProviderAuthRequirement = {
  code: typeof PROVIDER_AUTH_REQUIRED_CODE;
  recoveryCommand: string;
  seq: number;
};

type ProviderAuthEvent = {
  seq: number;
  type: string;
  [key: string]: unknown;
};

const RECOVERY_PROVING_EVENT_TYPES = new Set([
  "message_delta",
  "message_complete",
  "thinking_delta",
  "thinking_complete",
  "tool_call",
  "tool_call_update",
  "tool_result",
  "plan_update",
  "permission_request",
  "turn_metrics",
]);

export function deriveProviderAuthRequirement(
  events: readonly ProviderAuthEvent[],
  confirmedThroughSeq = 0,
): ProviderAuthRequirement | null {
  let requirement: ProviderAuthRequirement | null = null;
  for (const event of events) {
    if (
      event.type === "error" &&
      event.code === PROVIDER_AUTH_REQUIRED_CODE &&
      typeof event.recovery_command === "string" &&
      event.recovery_command.length > 0 &&
      event.seq > confirmedThroughSeq
    ) {
      requirement = {
        code: PROVIDER_AUTH_REQUIRED_CODE,
        recoveryCommand: event.recovery_command,
        seq: event.seq,
      };
    } else if (
      requirement &&
      event.seq > requirement.seq &&
      RECOVERY_PROVING_EVENT_TYPES.has(event.type)
    ) {
      requirement = null;
    }
  }
  return requirement;
}
