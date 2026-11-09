/**
 * B3-T4: Extract markdown from AI output that may be wrapped in JSON.
 *
 * The bug: handleFullDraftApply detected JSON-shaped output and immediately
 * toast.error'd + returned -- the user waited tens of seconds for nothing.
 * Fix: try to unwrap the JSON and extract the content/markdown/text field.
 *
 * Pure function so it's unit-testable without DOM.
 */

/**
 * Attempt to extract markdown content from a potentially JSON-wrapped AI output.
 *
 * @returns The extracted markdown string, or null if extraction failed
 *          (caller should show the original error message).
 */
export function extractMarkdownFromAiOutput(raw: string): string | null {
  const trimmed = raw.trim();

  // Only attempt JSON extraction if it starts with { or [
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) {
    // Not JSON -- return as-is (legitimate markdown content)
    return raw;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    // Starts with {/[ but isn't valid JSON -- might be legitimate content, allow
    return raw;
  }

  // Try common field names for the content
  if (parsed && typeof parsed === "object") {
    const obj = parsed as Record<string, unknown>;
    const fields = ["content", "markdown", "text", "result", "draft"];
    for (const field of fields) {
      const val = obj[field];
      if (typeof val === "string" && val.trim().length > 0) {
        return val;
      }
    }
  }

  // JSON parsed but no known field has string content -> null (show error)
  return null;
}
