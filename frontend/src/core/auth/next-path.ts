/**
 * Post-auth redirect target validation.
 *
 * Prevent open redirect attacks: only same-origin relative paths may be
 * used as a redirect target after login / setup.
 * Per RFC-001: Only allow relative paths starting with /
 */

export const DEFAULT_AUTH_NEXT_PATH = "/workspace";

export function validateNextParam(next: string | null): string | null {
  if (!next) {
    return null;
  }

  // Need start with / (relative path)
  if (!next.startsWith("/")) {
    return null;
  }

  // Disallow protocol-relative URLs
  if (next.startsWith("//")) {
    return null;
  }

  // Backslashes are the dangerous case: WHATWG URL parsing folds `\` into `/`,
  // so `/\evil.com` resolves to `http://evil.com/` despite passing the two
  // checks above. Reject them anywhere in the string, not just at the front.
  // Colons are rejected outright to keep scheme-bearing targets
  // (`javascript:`, `data:`) out — no legitimate redirect target needs one.
  if (next.includes("\\") || next.includes(":")) {
    return null;
  }

  // Valid relative path
  return next;
}

export function resolveNextPath(
  next: string | null,
  fallback: string = DEFAULT_AUTH_NEXT_PATH,
): string {
  return validateNextParam(next) ?? fallback;
}
