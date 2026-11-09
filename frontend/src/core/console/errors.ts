/**
 * Shared error-message helper for the console panels.
 *
 * Extracted from `console-page.tsx`'s former top-level banner so each panel
 * can render its own inline error state with the same 503/403 mapping.
 */

import { ConsoleApiError } from "./api";

/** Resolve an error to a user-facing message using the console i18n bundle. */
export function consoleErrorMessage(
  error: unknown,
  c: { errorNoDatabase: string; errorForbidden: string },
): string {
  if (error instanceof ConsoleApiError) {
    // 503 has a specific, actionable cause: the memory DB backend keeps no
    // run history, so there is nothing to report on.
    if (error.status === 503) return c.errorNoDatabase;
    if (error.status === 403) return c.errorForbidden;
    return error.message;
  }
  return String(error);
}
