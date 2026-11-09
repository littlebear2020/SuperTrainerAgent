/**
 * Inline token-based LCS diff for the selection popover's 改写/扩写 preview
 * (Task W8, spec §4.3).
 *
 * Why inline: no diff library is a frontend dependency (offline/domestic
 * constraint — see deployment-constraint-domestic-only). A simple LCS over
 * CJK-aware tokens is enough for a "简易 diff" (original selection vs streamed
 * result) so the user reviews changes before applying.
 *
 * Tokenization (reuses the CJK idea from count-words.ts):
 * - CJK ideograph / kana / hangul → ONE token per character (so Chinese
 *   changes surface at character granularity — `你好` → `你好世界` shows
 *   `世界` as added, not the whole string as replaced).
 * - Latin letters / digits / fullwidth letters-digits → ONE token per
 *   whitespace-delimited word (`hello` is one token, not five).
 * - Whitespace runs → ONE token (preserved so spacing changes are visible).
 * - Everything else (punctuation, symbols) → ONE token per character.
 *
 * The LCS is the classic O(n·m) DP + backtrack. Consecutive segments of the
 * same type are merged so `世` + `界` render as a single `added "世界"`.
 *
 * Pure: no React, no DOM. DiffView (the rendering) lives in diff-view.tsx.
 */

import type { AssistOp } from "@/core/writer/types";

import { isCJKWordChar } from "./text-utils";

// ── Types ──────────────────────────────────────────────────────────────────

export interface DiffSegment {
  type: "added" | "removed" | "unchanged";
  text: string;
}

interface DiffToken {
  text: string;
}

// ── shouldShowDiff ──────────────────────────────────────────────────────────

/**
 * Whether the preview should show a diff (original vs result) for the op.
 *
 * - rewrite / expand → true (large rewrites: the user benefits from seeing
 *   what changed vs the original before applying — spec §4.3).
 * - polish → false (small whole-text touch-up; the bare result is enough).
 * - summarize → false (insert-below, non-destructive; no diff needed).
 */
export function shouldShowDiff(op: AssistOp): boolean {
  return op === "rewrite" || op === "expand";
}

// ── Tokenization ───────────────────────────────────────────────────────────

/** Whitespace (incl. ideographic space U+3000). */
function isWhitespaceChar(code: number): boolean {
  return (
    code === 0x20 || // space
    code === 0x09 || // tab
    code === 0x0a || // LF
    code === 0x0d || // CR
    code === 0x0c || // form feed
    code === 0x0b || // vertical tab
    code === 0x3000 // ideographic space
  );
}

/**
 * A latin "word character" — ASCII letter/digit or fullwidth letter/digit
 * (U+FF00–FFEF range). These form one token per whitespace-delimited word.
 * (Fullwidth Ａ-Ｚ ａ-ｚ ０-９ count as latin, matching count-words.ts.)
 */
function isLatinWordChar(code: number): boolean {
  if (code >= 0x61 && code <= 0x7a) return true; // a-z
  if (code >= 0x41 && code <= 0x5a) return true; // A-Z
  if (code >= 0x30 && code <= 0x39) return true; // 0-9
  if (code >= 0xff10 && code <= 0xff19) return true; // fullwidth ０-９
  if (code >= 0xff21 && code <= 0xff3a) return true; // fullwidth Ａ-Ｚ
  if (code >= 0xff41 && code <= 0xff5a) return true; // fullwidth ａ-ｚ
  return false;
}

/** Advance by 1 or 2 UTF-16 code units depending on whether `code` is supplementary. */
function stepFor(code: number): number {
  return code > 0xffff ? 2 : 1;
}

/**
 * Tokenize text for diffing.
 *
 * - CJK char → 1 token (char granularity for Chinese).
 * - Latin word (run of letters/digits) → 1 token (word granularity).
 * - Whitespace run → 1 token (preserved; spacing changes are visible).
 * - Other (punctuation/symbols) → 1 token per character.
 */
function tokenizeForDiff(text: string): DiffToken[] {
  const tokens: DiffToken[] = [];
  let i = 0;
  while (i < text.length) {
    const code = text.codePointAt(i)!;

    if (isCJKWordChar(code)) {
      tokens.push({ text: String.fromCodePoint(code) });
      i += stepFor(code);
      continue;
    }

    if (isWhitespaceChar(code)) {
      let j = i;
      while (j < text.length && isWhitespaceChar(text.codePointAt(j)!)) {
        j += stepFor(text.codePointAt(j)!);
      }
      tokens.push({ text: text.slice(i, j) });
      i = j;
      continue;
    }

    if (isLatinWordChar(code)) {
      let j = i;
      while (j < text.length && isLatinWordChar(text.codePointAt(j)!)) {
        j += stepFor(text.codePointAt(j)!);
      }
      tokens.push({ text: text.slice(i, j) });
      i = j;
      continue;
    }

    // Punctuation / symbol / other — one token per character.
    tokens.push({ text: String.fromCodePoint(code) });
    i += stepFor(code);
  }
  return tokens;
}

// ── LCS diff ────────────────────────────────────────────────────────────────

/** Merge consecutive segments of the same type into one. */
function mergeSegments(segments: DiffSegment[]): DiffSegment[] {
  const merged: DiffSegment[] = [];
  for (const seg of segments) {
    const last = merged[merged.length - 1];
    if (last?.type === seg.type) {
      last.text += seg.text;
    } else {
      merged.push({ ...seg });
    }
  }
  return merged;
}

/**
 * Compute a token-based LCS diff between `original` and `revised`.
 *
 * Returns ordered segments `{type, text}`: removed (only in original),
 * added (only in revised), unchanged (in both). Consecutive same-type
 * segments are merged. Pure — no side effects, no React.
 *
 * The classic O(n·m) LCS DP + backtrack. Sufficient for selection-sized
 * text (a few hundred to low thousands of characters); not a full-text
 * diff (that's W10, out of scope).
 */
export function computeDiff(original: string, revised: string): DiffSegment[] {
  const a = tokenizeForDiff(original);
  const b = tokenizeForDiff(revised);
  const n = a.length;
  const m = b.length;

  // dp[i][j] = length of the LCS of a[i:] and b[j:].
  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      if (a[i]!.text === b[j]!.text) {
        dp[i]![j] = dp[i + 1]![j + 1]! + 1;
      } else {
        const down = dp[i + 1]![j]!;
        const right = dp[i]![j + 1]!;
        dp[i]![j] = Math.max(down, right);
      }
    }
  }

  // Backtrack: walk both sequences, emitting removed/added/unchanged.
  const segments: DiffSegment[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i]!.text === b[j]!.text) {
      segments.push({ type: "unchanged", text: a[i]!.text });
      i++;
      j++;
    } else if (dp[i + 1]![j]! >= dp[i]![j + 1]!) {
      segments.push({ type: "removed", text: a[i]!.text });
      i++;
    } else {
      segments.push({ type: "added", text: b[j]!.text });
      j++;
    }
  }
  // Tail: remaining tokens only in one sequence.
  while (i < n) {
    segments.push({ type: "removed", text: a[i]!.text });
    i++;
  }
  while (j < m) {
    segments.push({ type: "added", text: b[j]!.text });
    j++;
  }

  return mergeSegments(segments);
}

/** Cell-product ceiling for the O(n×m) LCS DP matrix — shared by the
 * full-draft gate and the selection-popover gate (P1-10). */
export const DIFF_MAX_CELL_PRODUCT = 25_000_000;

/** Above the ceiling, skip the inline diff and render a plain before/after view. */
export function shouldUseDiffView(original: string, revised: string): boolean {
  return (original.length + 1) * (revised.length + 1) <= DIFF_MAX_CELL_PRODUCT;
}
