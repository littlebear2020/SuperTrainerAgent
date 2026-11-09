/**
 * Structured citation-list parsing for the co-writer's web-search output.
 *
 * When the co-writer uses web-search sources, it appends a
 * `[[CITATIONS:<json>]]` marker at the END of its response (after the body).
 * The `<json>` is an array of `{title, url, org}` (org = site/domain/机构).
 *
 * This helper detects the trailing marker, parses the JSON array, and returns
 * the citations plus the body with the marker stripped — mirroring the W13
 * `parseStageSignal` text-marker pattern (but at the END rather than the
 * start).
 *
 * Tolerant contract: malformed JSON → null citations, no crash. The marker is
 * stripped from the body even when malformed so the broken token never leaks
 * into the visible text.
 *
 * Used by `full-draft-modal.tsx` (heavy/web path) to render a citation list
 * below the result. The light path (web unchecked) has no search → no
 * citations → no list.
 */

// ── Types ────────────────────────────────────────────────────────────────

export interface Citation {
  /** Title of the source page/article. */
  title: string;
  /** Full URL of the source (clickable). */
  url: string;
  /** Site/domain/机构 — the publishing organization. */
  org: string;
}

export interface CitationParseResult {
  /** Parsed citations, or null when no valid marker is present. */
  citations: Citation[] | null;
  /** The input text with the trailing marker (and preceding whitespace) stripped. */
  body: string;
}

// ── Marker parser ────────────────────────────────────────────────────────

/**
 * Tail validator for the LAST `[[CITATIONS:<json>]]` marker.
 *
 * P1-6: `parseCitations` locates the marker via `lastIndexOf` (not a global
 * greedy regex) so a marker echoed mid-body — the model restating the rule,
 * or a doc discussing the feature — no longer causes everything after it to
 * be deleted. This regex only validates that the slice from the last marker
 * to end-of-string is a well-formed `[[CITATIONS:<json>]]` with optional
 * trailing whitespace.
 */
const CITATIONS_TAIL_RE = /^\[\[CITATIONS:([\s\S]+)\]\]\s*$/;

/**
 * Detect a trailing `[[CITATIONS:<json>]]` marker and return the parsed
 * citations plus the body with the marker stripped.
 *
 * Behavior:
 * - No marker → `{ citations: null, body: <unchanged> }`.
 * - Valid marker + valid JSON array → `{ citations: [...], body: <marker stripped> }`.
 * - Valid marker + malformed JSON → `{ citations: null, body: <marker stripped> }`
 *   (the broken marker is stripped so it doesn't leak into the visible text).
 * - Valid marker + empty array `[]` → `{ citations: null, body: <marker stripped> }`
 *   (no list to render).
 * - Array items missing required string fields (`title`/`url`/`org`) are
 *   silently skipped; if no items survive, citations is null.
 */
export function parseCitations(raw: string): CitationParseResult {
  const marker = "[[CITATIONS:";
  const lastIdx = raw.lastIndexOf(marker);
  if (lastIdx === -1) {
    return { citations: null, body: raw };
  }
  // 校验从最后一个标记到末尾整体匹配 `[[CITATIONS:<json>]]`（允许尾部空白）。
  // 用 lastIndexOf 而非全局贪婪正则：避免正文中段出现标记时吞掉其后全部正文（P1-6）。
  const match = CITATIONS_TAIL_RE.exec(raw.slice(lastIdx));
  if (!match) {
    return { citations: null, body: raw };
  }

  const jsonStr = match[1] ?? "";
  // Body = 最后一个标记之前的全部文本，去掉与标记之间的尾部空白。
  const body = raw.slice(0, lastIdx).replace(/\s+$/, "");

  try {
    const parsed: unknown = JSON.parse(jsonStr);
    if (!Array.isArray(parsed)) {
      return { citations: null, body };
    }

    const citations: Citation[] = [];
    for (const item of parsed) {
      if (
        item &&
        typeof item === "object" &&
        typeof (item as Record<string, unknown>).title === "string" &&
        typeof (item as Record<string, unknown>).url === "string" &&
        typeof (item as Record<string, unknown>).org === "string"
      ) {
        const c = item as { title: string; url: string; org: string };
        citations.push({ title: c.title, url: c.url, org: c.org });
      }
    }

    // Empty array or all items malformed → null (no list to render).
    return { citations: citations.length > 0 ? citations : null, body };
  } catch {
    // Malformed JSON: strip the marker from the body so the broken token
    // doesn't leak into the visible text; return null citations.
    return { citations: null, body };
  }
}

// ── URL scheme allowlist ─────────────────────────────────────────────────

// Moved to core/utils/url.ts (SEC-M18: shared with tool-result links in
// message-group.tsx). Re-exported here so writer imports keep working.
export { sanitizeCitationUrl } from "@/core/utils/url";
