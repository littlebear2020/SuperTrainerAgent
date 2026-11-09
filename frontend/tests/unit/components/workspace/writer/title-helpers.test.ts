/**
 * Title-derivation helper tests (Task W27 — §9.4 title consistency).
 *
 * Defect §9.4: the editor's `displayTitle` shows the first `# H1` heading
 * (regex `/^#\s+(.+)$/m`), but the stored `title` field is never updated
 * from it — only creation default + manual rename via `commitTitle`. So a
 * doc whose body starts with `# 我的架构实践` shows "我的架构实践" in the
 * editor header but "未命名文章" in the list. The autosave now writes the
 * title derived from the first H1 when it differs from the stored title.
 *
 * Pure helper extracted from writer-page.tsx for DOM-free testing:
 * - deriveTitleFromMarkdown: first `# H1` heading text, else fallback
 *
 * Mirrors force-snapshot.test.ts / fill-confirm-helpers.test.ts.
 */

import { expect, test } from "vitest";

import {
  deriveTitleFromMarkdown,
  shouldDeriveTitleFromH1,
} from "@/components/workspace/writer/writer-page";

// ── deriveTitleFromMarkdown ──────────────────────────────────────────────

test("deriveTitleFromMarkdown returns the first H1 heading text", () => {
  expect(deriveTitleFromMarkdown("# 我的标题\n正文", "fallback")).toBe(
    "我的标题",
  );
});

test("deriveTitleFromMarkdown returns fallback when only H2+ headings exist", () => {
  // `## 副标题` is not a single-`#` H1 → no match → fallback.
  expect(deriveTitleFromMarkdown("## 副标题", "当前标题")).toBe("当前标题");
});

test("deriveTitleFromMarkdown returns fallback for empty markdown", () => {
  expect(deriveTitleFromMarkdown("", "当前标题")).toBe("当前标题");
});

test("deriveTitleFromMarkdown returns fallback when no heading at all", () => {
  expect(deriveTitleFromMarkdown("正文内容，没有标题", "fallback")).toBe(
    "fallback",
  );
});

test("deriveTitleFromMarkdown does not match H1 buried mid-line (only start-of-line)", () => {
  // The `#` must be at the start of a line (regex uses `^` with `m` flag).
  expect(deriveTitleFromMarkdown("正文 # 不是标题", "fallback")).toBe(
    "fallback",
  );
});

test("deriveTitleFromMarkdown returns the FIRST H1 when multiple exist", () => {
  expect(deriveTitleFromMarkdown("# 第一标题\n\n# 第二标题", "fallback")).toBe(
    "第一标题",
  );
});

// ── B2-T6: shouldDeriveTitleFromH1 prefix gate ───────────────────────────

test("B2-T6: shouldDeriveTitleFromH1 returns true for default '未命名' title", () => {
  expect(shouldDeriveTitleFromH1("# 新标题\n正文", "未命名文章", false)).toBe(
    true,
  );
});

test("B2-T6: shouldDeriveTitleFromH1 returns false for manually renamed title", () => {
  expect(shouldDeriveTitleFromH1("# 新标题", "我的文章", false)).toBe(false);
});

test("B2-T6: shouldDeriveTitleFromH1 returns false when manuallyRenamed is true", () => {
  expect(shouldDeriveTitleFromH1("# 新标题", "未命名文章", true)).toBe(false);
});

test("B2-T6: shouldDeriveTitleFromH1 returns false for non-'未命名' title (cross-session guard)", () => {
  // A title that doesn't start with "未命名" should never be overwritten by H1
  // derivation, even if manuallyRenamed is false (e.g. after a page reload).
  expect(shouldDeriveTitleFromH1("# 新标题", "我的文章", false)).toBe(false);
});

test("B2-T6: shouldDeriveTitleFromH1 returns false when derived equals current", () => {
  expect(shouldDeriveTitleFromH1("# 未命名文章", "未命名文章", false)).toBe(
    false,
  );
});
