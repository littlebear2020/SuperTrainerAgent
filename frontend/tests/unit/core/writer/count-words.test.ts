/**
 * CJK-aware word count tests.
 *
 * Bug: the editor word count used `markdown.trim().split(/\s+/).length`, which
 * is meaningless for Chinese — a 1000-char essay with no spaces reports 1.
 * The replacement `countWords` counts CJK characters individually and latin
 * runs by whitespace-separated word, after stripping markdown markup.
 *
 * Counting rule (pinned by these tests):
 * - CJK ideographs / kana / hangul → each char = 1 "word".
 * - CJK punctuation (U+3000–303F, fullwidth forms U+FF00–FFEF, excluding
 *   fullwidth letters/digits) → SKIPPED (not counted), and acts as a word
 *   boundary. Punctuation is not "字".
 * - Latin letters, digits, ASCII punctuation, and fullwidth letters/digits →
 *   whitespace-separated words (the old `split(/\s+/)` behavior), with CJK
 *   chars/punctuation also delimiting latin runs.
 * - Markdown markers (`#`, `*`, list `-`/`1.`, code fences, `[]()`, pipes,
 *   blockquote `>`) are stripped before counting.
 * - Empty / whitespace-only / markup-only input → 0.
 */

import { describe, expect, test } from "vitest";

import { countWords } from "@/core/writer/count-words";

describe("countWords", () => {
  test("pure chinese counts by character", () => {
    expect(countWords("今天天气真好")).toBe(6);
  });

  test("pure english counts by word", () => {
    expect(countWords("the quick brown fox")).toBe(4);
  });

  test("mixed cjk and latin counts both", () => {
    // 你,好 = 2 CJK chars + "world","hello" = 2 latin words → 4
    expect(countWords("你好world hello")).toBe(4);
  });

  test("markdown markers excluded", () => {
    // "# 标题\n\n- 列表项\n**bold**"
    // After stripping markers: 标题(2) + 列表项(3) + bold(1) = 6.
    // `#`, `-`, `**`, and newlines must not inflate the count.
    expect(countWords("# 标题\n\n- 列表项\n**bold**")).toBe(6);
  });

  test("empty and markup-only input returns 0", () => {
    expect(countWords("")).toBe(0);
    expect(countWords("   ")).toBe(0);
    expect(countWords("#")).toBe(0); // only a heading marker
  });

  test("cjk punctuation is skipped (not counted)", () => {
    // 你好，世界！→ 你,好,世,界 = 4; fullwidth ，and ！are skipped.
    expect(countWords("你好，世界！")).toBe(4);
  });
});
