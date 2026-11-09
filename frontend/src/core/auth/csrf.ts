/**
 * Bootstrap the gateway's ``csrf_token`` cookie (Double Submit pattern).
 *
 * The gateway's CSRF middleware only sets ``csrf_token`` on auth-endpoint POST
 * responses (login/register/logout/initialize). A browser that never logs in —
 * no-auth / dev-bypass mode where ``/api/v1/auth/me`` returns a synthetic user
 * — therefore never receives the cookie, and every state-changing Gateway POST
 * (e.g. ``POST /api/writer/docs``) is rejected with 403, silently breaking
 * create/update/delete across the app.
 *
 * Calling this once on app mount hits ``GET /api/v1/auth/csrf-token`` which
 * sets the cookie regardless of auth state. GET is CSRF-exempt, so no token is
 * required to call it. The cookie is JS-readable so ``fetcher.ts`` echoes it
 * back as ``X-CSRF-Token`` on subsequent writes.
 */

/**
 * Fetch the csrf-token cookie. Never throws — a missing cookie only blocks
 * write operations, it must not prevent the page from loading.
 */
export async function bootstrapCsrfToken(): Promise<void> {
  try {
    await fetch("/api/v1/auth/csrf-token", { credentials: "include" });
  } catch {
    // Silent: write operations will surface their own 403 via per-mutation
    // onError toasts; we don't want a mount-time failure to block the UI.
  }
}
