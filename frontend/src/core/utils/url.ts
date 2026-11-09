// ── URL scheme allowlist (SEC-M18, shared) ─────────────────────────────────
// Moved from core/writer/citations.ts (P1-13) so tool-result links outside
// the markdown pipeline can share the same predicate; citations.ts re-exports
// it for writer compatibility.

/**
 * Allow only http/https (and protocol-relative) external URLs.
 *
 * LLM-streamed JSON and tool results are untrusted: a poisoned search result
 * could smuggle a `javascript:` URL into a rendered `<a href>` / `<img src>`.
 * Reject anything that isn't http(s) or `//`-prefixed; the caller renders
 * rejected URLs as non-clickable plain text. (For `<img src>` specifically,
 * callers should additionally require an `http`/`https` prefix — protocol
 * relative `//` is allowed for anchors but not needed for images.)
 */
export function sanitizeCitationUrl(url: string): string | null {
  const trimmed = url.trim();
  if (trimmed.startsWith("//")) return trimmed;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") {
      return trimmed;
    }
    return null;
  } catch {
    return null;
  }
}
