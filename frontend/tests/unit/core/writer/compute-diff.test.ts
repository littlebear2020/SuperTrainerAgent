/**
 * Selection diff-preview tests (Task W8).
 *
 * Spec §4.3: for 改写/扩写 (large rewrites) the popover must show a simple
 * diff (original selection vs streamed result) so the user reviews changes
 * before applying. polish/summarize keep their bare-result preview (no diff).
 *
 * These tests pin:
 * - shouldShowDiff: rewrite/expand → true; polish/summarize → false.
 * - computeDiff: pure token-based LCS over CJK-char/latin-word tokens, so
 *   Chinese changes show at character granularity. Segments are
 *   {type:"added"|"removed"|"unchanged", text} with consecutive same-type
 *   segments merged.
 *
 * No external diff lib is available (offline/domestic constraint — see
 * deployment-constraint-domestic-only). The diff is an inline LCS.
 */

import { describe, expect, test } from "vitest";

import {
  computeDiff,
  DIFF_MAX_CELL_PRODUCT,
  shouldShowDiff,
  shouldUseDiffView,
} from "@/core/writer/compute-diff";

describe("shouldShowDiff", () => {
  test("rewrite and expand show diff; polish and summarize do not", () => {
    expect(shouldShowDiff("rewrite")).toBe(true);
    expect(shouldShowDiff("expand")).toBe(true);
    expect(shouldShowDiff("polish")).toBe(false);
    expect(shouldShowDiff("summarize")).toBe(false);
  });
});

describe("computeDiff", () => {
  test("unchanged: identical text → one unchanged segment, no added/removed", () => {
    const segs = computeDiff("abc", "abc");
    expect(segs).toEqual([{ type: "unchanged", text: "abc" }]);
    expect(segs.some((s) => s.type === "added")).toBe(false);
    expect(segs.some((s) => s.type === "removed")).toBe(false);
  });

  test("added: 你好 → 你好世界 yields unchanged '你好' + added '世界' (CJK char granularity)", () => {
    const segs = computeDiff("你好", "你好世界");
    expect(segs).toEqual([
      { type: "unchanged", text: "你好" },
      { type: "added", text: "世界" },
    ]);
  });

  test("removed: 你好世界 → 你好 yields unchanged '你好' + removed '世界'", () => {
    const segs = computeDiff("你好世界", "你好");
    expect(segs).toEqual([
      { type: "unchanged", text: "你好" },
      { type: "removed", text: "世界" },
    ]);
  });

  test("replaced: 旧内容 → 新内容 yields removed '旧' + added '新' + unchanged '内容'", () => {
    // LCS finds the common suffix '内容'. The leading char differs, so the
    // diff is [removed '旧', added '新', unchanged '内容'] — NOT a single
    // removed '旧内容' + added '新内容' block. This is the minimal-edit
    // form our LCS produces; pinned here.
    const segs = computeDiff("旧内容", "新内容");
    expect(segs).toEqual([
      { type: "removed", text: "旧" },
      { type: "added", text: "新" },
      { type: "unchanged", text: "内容" },
    ]);
  });

  test("empty original: everything added", () => {
    const segs = computeDiff("", "新文本");
    expect(segs).toEqual([{ type: "added", text: "新文本" }]);
  });

  test("empty revised: everything removed", () => {
    const segs = computeDiff("旧文本", "");
    expect(segs).toEqual([{ type: "removed", text: "旧文本" }]);
  });

  test("latin words tokenize by word (not per char) but CJK by char", () => {
    // "hello world" → "hello" (unchanged) + " " (unchanged) + "world"→"words"
    // CJK char granularity: 世界 stays char-level.
    const segs = computeDiff("hello 世界", "hello 世界!");
    // trailing '!' added; the leading "hello 世界" matches.
    expect(segs).toEqual([
      { type: "unchanged", text: "hello 世界" },
      { type: "added", text: "!" },
    ]);
  });
});

describe("shouldUseDiffView (P1-10)", () => {
  test("allows small inputs", () => {
    expect(shouldUseDiffView("短文本", "改写后的短文本")).toBe(true);
  });

  test("rejects inputs whose cell product exceeds the ceiling", () => {
    const a = "x".repeat(5000);
    const b = "y".repeat(5000); // 5001 × 5001 = 25,010,001 > 25,000,000
    expect(shouldUseDiffView(a, b)).toBe(false);
  });

  test("boundary: exactly at the ceiling is allowed", () => {
    // (4999+1) × (4999+1) = 25,000,000 == ceiling
    const a = "x".repeat(4999);
    const b = "y".repeat(4999);
    expect(shouldUseDiffView(a, b)).toBe(true);
    expect(DIFF_MAX_CELL_PRODUCT).toBe(25_000_000);
  });
});
