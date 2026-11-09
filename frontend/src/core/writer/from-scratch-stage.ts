/**
 * Structured stage-signal parsing for "从零创作" (Create from Scratch).
 *
 * Replaces string-guessing (length>200 + heading + !includes("请问")) with a
 * structured marker the co-writer emits at the start of its message:
 * `[[STAGE:OUTLINE]]` for an outline, `[[STAGE:DRAFT]]` for a full draft.
 *
 * §11 phased approach: when the marker is absent (older SOULs / non-compliant
 * models), the legacy `isFullDocHeuristic` string-guessing fallback is used so
 * existing behavior is preserved until the SOUL reliably emits markers.
 *
 * Shared (DRY) by `use-from-scratch.ts` (buildResult) and the panel's
 * `MessageBubble` display heuristic.
 */

// ── Types ────────────────────────────────────────────────────────────────

export type ScratchStage = "outline" | "draft";

export interface StageSignal {
  /** Detected stage, or null when no marker is present (fallback path). */
  stage: ScratchStage | null;
  /** The input text with the leading marker stripped (unchanged if absent). */
  text: string;
}

// ── Marker parser ────────────────────────────────────────────────────────

/**
 * Detect a leading `[[STAGE:OUTLINE]]` / `[[STAGE:DRAFT]]` marker and return the
 * stage plus the text with the marker stripped.
 *
 * The marker must be at the very start of the message (no leading whitespace);
 * a marker embedded mid-text is ignored so body content mentioning the token
 * is never misclassified. Only the marker token itself is stripped — any
 * newlines following it are preserved (the model may write the marker on its
 * own first line then the content).
 */
export function parseStageSignal(raw: string): StageSignal {
  const match = /^\[\[STAGE:(OUTLINE|DRAFT)\]\]/.exec(raw);
  if (!match) {
    return { stage: null, text: raw };
  }
  const stage: ScratchStage = match[1] === "OUTLINE" ? "outline" : "draft";
  return { stage, text: raw.slice(match[0].length) };
}

// ── Legacy fallback (§11) ────────────────────────────────────────────────

/**
 * The legacy string-guessing heuristic for "is this a full document?".
 *
 * Used ONLY when no stage marker is present (the §11 fallback). Once the SOUL
 * reliably emits `[[STAGE:DRAFT]]`, this path is rarely hit. Kept here as the
 * single source of truth so buildResult and the panel share one definition.
 */
export function isFullDocHeuristic(text: string): boolean {
  return (
    text.length > 200 &&
    /^#{1,3}\s/m.test(text) &&
    !text.includes("请问") &&
    !text.includes("是否需要")
  );
}
