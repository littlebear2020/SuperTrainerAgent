/**
 * List-preview markdown-strip helper tests (Task W28 — §9.5 full strip).
 *
 * Defect §9.5: the list preview used `.slice(0,160).replace(/[#*\n]/g," ")`
 * — strips ONLY `#`, `*`, `\n`, so links `[text](url)`→`text (url)`, images
 * `![alt](url)`→`![alt](url)`, tables `| a | b |`→`| a | b |`, inline code
 * `` `code` `` → backticks remain → 乱码 in the list card. The fix uses a
 * full markdown-strip (reusing `stripMarkdownForCount` from count-words.ts)
 * to produce readable prose, then collapses whitespace + trims.
 *
 * Pure helper extracted from writer-page.tsx for DOM-free testing:
 * - stripMarkdownForPreview: full strip → readable prose string
 *
 * Mirrors force-snapshot.test.ts / fill-confirm-helpers.test.ts.
 */

import { expect, test } from "vitest";

import { stripMarkdownForPreview } from "@/components/workspace/writer/writer-page";

// ── stripMarkdownForPreview ─────────────────────────────────────────────

test("stripMarkdownForPreview strips links to text only (no URL)", () => {
  expect(stripMarkdownForPreview("[文本](https://x.com)")).toBe("文本");
});

test("stripMarkdownForPreview strips images to alt text only", () => {
  expect(stripMarkdownForPreview("![alt](https://x.com/x.png)")).toBe("alt");
});

test("stripMarkdownForPreview strips tables to cell text (no pipes/dashes)", () => {
  expect(stripMarkdownForPreview("| a | b |\n|---|---|\n| 1 | 2 |")).toBe(
    "a b 1 2",
  );
});

test("stripMarkdownForPreview strips inline code to text (no backticks)", () => {
  expect(stripMarkdownForPreview("`code`")).toBe("code");
});

test("stripMarkdownForPreview strips heading markers and list markers", () => {
  expect(stripMarkdownForPreview("# 标题\n- 项")).toBe("标题 项");
});

test("stripMarkdownForPreview collapses whitespace runs and trims", () => {
  // Multiple newlines + spaces → single space; leading/trailing trimmed.
  expect(stripMarkdownForPreview("  第一段\n\n\n  第二段  \n  ")).toBe(
    "第一段 第二段",
  );
});
