/**
 * CJK-aware word/character count for the Writer editor.
 *
 * The editor's word count previously used `markdown.trim().split(/\s+/).length`,
 * which is meaningless for Chinese — a 1000-character essay with no spaces
 * reports a count of 1. Since the product's primary language is Chinese
 * (`defaultTitle: "未命名文章"`), the count must be CJK-aware:
 *
 * - CJK ideographs (汉字), Japanese kana, Korean hangul → each character
 *   counts as 1 "word" (CJK has no word-spacing).
 * - Latin letters, digits, and ASCII punctuation → whitespace-separated
 *   words (the old `split(/\s+/)` behavior, correct for English). CJK
 *   characters/punctuation also delimit latin runs, so `你好world` counts
 *   你 + 好 + "world" = 3.
 * - CJK punctuation (fullwidth `，。！？` …) is SKIPPED — it is not "字" — and
 *   acts as a word boundary. (See `isCJKPunctuation` for the exact ranges.)
 * - Markdown markup (`#`, `*`, list markers, code fences, `[]()`, pipes,
 *   blockquotes) is stripped before counting so it never inflates the count.
 * - Empty / whitespace-only / markup-only input → 0.
 *
 * No external deps — regex stripping + a code-point loop only.
 */

import { isCJKWordChar, stripMarkdown } from "./text-utils";

/**
 * Count "words" in a markdown string, CJK-aware.
 *
 * CJK characters count by character; latin runs count by
 * whitespace-separated word; markdown markers and CJK punctuation are
 * excluded.
 */
export function countWords(markdown: string): number {
  const text = stripMarkdownForCount(markdown);
  if (!text.trim()) return 0;

  let count = 0;
  let inLatinWord = false;
  for (const ch of text) {
    const code = ch.codePointAt(0)!;
    if (isCJKWordChar(code)) {
      count++;
      inLatinWord = false;
    } else if (isWhitespace(ch)) {
      inLatinWord = false;
    } else if (isCJKPunctuation(code)) {
      // Skipped (not counted) — acts as a boundary so adjacent latin runs
      // don't merge across CJK punctuation.
      inLatinWord = false;
    } else {
      // Latin letter / digit / ASCII punctuation / fullwidth letter — part
      // of the current whitespace-separated latin run.
      if (!inLatinWord) {
        count++;
        inLatinWord = true;
      }
    }
  }
  return count;
}

/**
 * CJK punctuation — skipped (not counted) and treated as a boundary.
 * Ranges:
 * - U+3000–U+303F: CJK Symbols and Punctuation (、。「」etc.)
 * - U+FF00–U+FFEF: Halfwidth and Fullwidth Forms, EXCLUDING fullwidth
 *   latin letters (Ａ-Ｚ, ａ-ｚ) and digits (０-９), which count as latin.
 */
function isCJKPunctuation(code: number): boolean {
  if (code >= 0x3000 && code <= 0x303f) return true;
  if (code < 0xff00 || code > 0xffef) return false;
  if (code >= 0xff10 && code <= 0xff19) return false; // fullwidth digits ０-９ → latin
  if (code >= 0xff21 && code <= 0xff3a) return false; // fullwidth Ａ-Ｚ → latin
  if (code >= 0xff41 && code <= 0xff5a) return false; // fullwidth ａ-ｚ → latin
  return true;
}

/** Whitespace (incl. ideographic space, handled here as plain boundary). */
function isWhitespace(ch: string): boolean {
  return (
    ch === " " ||
    ch === "\t" ||
    ch === "\n" ||
    ch === "\r" ||
    ch === "\f" ||
    ch === "\v" ||
    ch === "　"
  );
}

/**
 * Strip markdown markup that would inflate the count, keeping the prose
 * text. Conservative and intentionally not a full markdown parser.
 *
 * Removed entirely (markup, not prose): fenced code blocks, horizontal
 * rules / setext underlines, table separator rows.
 * Markers removed, content kept: headings, bold/italic/strikethrough,
 * inline code, links, images (alt text kept), blockquotes, list markers.
 * Table pipes → spaces (cell text preserved).
 *
 * Exported so the writer list-preview (W28 §9.5) can reuse it — the
 * preview needs the same markup stripping (links→text, images→alt,
 * tables→cell text, etc.) before collapsing whitespace to a single line.
 */
export function stripMarkdownForCount(markdown: string): string {
  // 规则表收敛至 text-utils.ts（2026-10-06 合并批）；计数路径整删替换符 = " "
  return stripMarkdown(markdown, " ");
}
