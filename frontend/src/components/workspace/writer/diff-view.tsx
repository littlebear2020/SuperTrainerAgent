"use client";

import { useMemo } from "react";

import {
  computeDiff,
  shouldUseDiffView,
  type DiffSegment,
} from "@/core/writer/compute-diff";

/**
 * Read-only diff view rendered inside the selection popover's preview pane
 * for 改写/扩写 (Task W8, spec §4.3).
 *
 * Shows the original selection vs the streamed result as inline segments:
 * - removed (only in original) → red, struck-through.
 * - added (only in revised) → green, highlighted.
 * - unchanged (in both) → muted.
 *
 * Inline (no layout split) so the user reads the change as one flowing text,
 * like a tracked-changes view. Pure presentational — the diff is computed
 * by the pure `computeDiff` helper (tested in compute-diff.test.ts).
 *
 * P1-10: above the cell-product ceiling the O(n×m) LCS matrix would blow up
 * (a full-doc selection is ~20k chars → 400M cells) — fall back to a plain
 * before/after view. The computation is also memoized so re-renders during
 * streaming don't re-run the DP every frame.
 */
export function DiffView({
  original,
  revised,
}: {
  original: string;
  revised: string;
}) {
  const useDiff = shouldUseDiffView(original, revised);
  const segments: DiffSegment[] = useMemo(
    () => (useDiff ? computeDiff(original, revised) : []),
    [useDiff, original, revised],
  );

  if (!useDiff) {
    // 超大选区：跳过 O(n×m) diff，降级为前后对照（P1-10）
    return (
      <div className="max-h-[420px] space-y-3 overflow-y-auto text-[12px] leading-relaxed">
        <div>
          <div className="mb-1 text-[var(--muted-foreground)]">原文</div>
          <div className="whitespace-pre-wrap">{original}</div>
        </div>
        <div>
          <div className="mb-1 text-[var(--muted-foreground)]">改写后</div>
          <div className="whitespace-pre-wrap">{revised}</div>
        </div>
      </div>
    );
  }

  if (segments.length === 0) return null;

  return (
    <div className="max-h-[420px] overflow-y-auto text-[12px] leading-relaxed">
      {segments.map((seg, i) => {
        if (seg.type === "removed") {
          return (
            <span
              key={i}
              className="rounded bg-red-500/10 text-red-600 line-through decoration-red-400/60 dark:text-red-400"
            >
              {seg.text}
            </span>
          );
        }
        if (seg.type === "added") {
          return (
            <span
              key={i}
              className="rounded bg-green-500/10 text-green-700 dark:text-green-300"
            >
              {seg.text}
            </span>
          );
        }
        return (
          <span key={i} className="text-[var(--muted-foreground)]">
            {seg.text}
          </span>
        );
      })}
    </div>
  );
}
