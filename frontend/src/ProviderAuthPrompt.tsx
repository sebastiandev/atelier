import type { ProviderAuthRequirement } from "./providerAuth";

export function ProviderAuthPrompt({
  requirement,
  onConfirm,
}: {
  requirement: ProviderAuthRequirement;
  onConfirm: () => void;
}) {
  return (
    <section
      className="provider-auth-prompt"
      role="alert"
      aria-label="Provider sign-in required"
    >
      <div className="provider-auth-copy">
        <strong>Claude sign-in required</strong>
        <span>Run this command in a terminal, then confirm.</span>
        <code>{requirement.recoveryCommand}</code>
      </div>
      <button type="button" className="btn primary sm" onClick={onConfirm}>
        I've signed in
      </button>
    </section>
  );
}
