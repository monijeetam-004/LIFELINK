/** A v4 UUID via the Web Crypto API (available in every modern browser and
 * in the test/build environment) -- used only as a client-generated
 * idempotency key, never anything security-sensitive.
 *
 * Shared by every submission flow that needs one (SosModal, BloodRequestModal)
 * so the "generate once per attempt, reuse across retries of that same
 * attempt, mint a new one only for a genuinely new attempt" contract lives
 * in exactly one place rather than being copy-pasted per form. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  // Fallback for an environment without crypto.randomUUID (older browsers).
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}
