/** Apply companion theme CSS custom properties to ``document.documentElement``. */

let appliedTokenKeys: string[] = [];

/**
 * Set theme tokens on ``<html>``. Clears previously applied keys first so
 * partial packs do not leave stale overrides from another theme.
 */
export function applyThemeTokens(tokens: Record<string, string>): void {
  const root = document.documentElement;
  for (const key of appliedTokenKeys) {
    root.style.removeProperty(key);
  }
  const nextKeys: string[] = [];
  for (const [key, value] of Object.entries(tokens)) {
    if (!key.startsWith("--") || typeof value !== "string") continue;
    root.style.setProperty(key, value);
    nextKeys.push(key);
  }
  appliedTokenKeys = nextKeys;
}

/** Test helper: reset tracking of applied keys. */
export function resetAppliedThemeTokensForTests(): void {
  appliedTokenKeys = [];
}
