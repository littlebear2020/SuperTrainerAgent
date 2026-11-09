/**
 * Pending-prompt handoff between routes (updated 2026-09-04, SEC-H11 批次 1).
 *
 * The sessionStorage JSON entry (`{prompt, autoSend}`) is the ONLY channel that
 * may trigger an automatic send — the gallery 复刻 button and the video-gen
 * Remotion/ViMax jump both hand off through it regardless of prompt length
 * (URL `?msg=` used to carry short prompts before SEC-H11; it no longer does).
 *
 * `?msg=` survives purely as a PREFILL of the input box (never autosend) — the
 * agent chat route resolves it via this reader. `readPendingPrompt` fills and
 * lets the user edit/send (NO autosend). It is a pure function over its args so
 * it is unit-testable without a DOM; callers pass `sessionStorage` (guarded for
 * SSR). The sessionStorage entry is consumed on read so it does not linger or
 * re-fill on refresh.
 */

export const PENDING_PROMPT_KEY = "deerflow_pending_prompt";

type PendingPromptEntry = { prompt?: unknown; autoSend?: unknown };

/**
 * Resolve the initial input for a chat route. Priority: `?msg=` URL param
 * (prefill only — never sends, SEC-H11), then `sessionStorage` JSON
 * `{prompt, autoSend}`. Returns "" when there is nothing to fill. SSR-safe: pass
 * `null`/`undefined` for storage (e.g. `typeof window === "undefined" ? null :
 * sessionStorage`).
 */
export function readPendingPrompt(
  msgParam: string | null,
  storage: Storage | null | undefined,
): string {
  if (msgParam) {
    return msgParam;
  }
  if (!storage) {
    return "";
  }
  try {
    const raw = storage.getItem(PENDING_PROMPT_KEY);
    if (!raw) {
      return "";
    }
    storage.removeItem(PENDING_PROMPT_KEY); // consume — don't linger / re-fill on refresh
    const parsed = JSON.parse(raw) as PendingPromptEntry;
    if (parsed && typeof parsed.prompt === "string") {
      return parsed.prompt;
    }
    return "";
  } catch {
    // malformed entry — ignore, fall back to empty (no throw to the caller)
    return "";
  }
}
