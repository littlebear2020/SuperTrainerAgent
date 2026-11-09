/**
 * Fill-into-editor confirm helper tests (Task W17 — §5.2E).
 *
 * Defect §5.1.5: from-scratch "填入编辑器" (handleFillEditor → setContentViaEditor
 * → wholesale setContent) overwrites the existing doc without confirm. Spec §5.2E:
 * if the editor is non-empty, confirm 替换/追加/取消; if empty, fill directly.
 *
 * Three pure helpers are extracted from writer-page.tsx for DOM-free testing:
 * - shouldConfirmFill: non-empty after trim → true
 * - buildAppendContent: existing + "\n\n" + newMarkdown
 * - dispatchFillAction + shouldForceSnapshotForFill: replace/append/cancel routing
 *
 * Mirrors force-snapshot.test.ts: pure-function tests with vi mocks.
 */

import { expect, test, vi } from "vitest";

import {
  buildAppendContent,
  dispatchFillAction,
  shouldConfirmFill,
  shouldForceSnapshotForFill,
  type FillChoice,
} from "@/components/workspace/writer/writer-page";

// ── shouldConfirmFill ────────────────────────────────────────────────────

test("shouldConfirmFill returns true for non-empty content", () => {
  expect(shouldConfirmFill("# 标题\n内容")).toBe(true);
});

test("shouldConfirmFill returns true for whitespace-only content with real chars", () => {
  expect(shouldConfirmFill("  有内容  ")).toBe(true);
});

test("shouldConfirmFill returns false for empty string", () => {
  expect(shouldConfirmFill("")).toBe(false);
});

test("shouldConfirmFill returns false for whitespace-only string", () => {
  expect(shouldConfirmFill("   ")).toBe(false);
});

test("shouldConfirmFill returns false for newline-only string", () => {
  expect(shouldConfirmFill("\n\n\n")).toBe(false);
});

// ── buildAppendContent ───────────────────────────────────────────────────

test("buildAppendContent joins existing and new with blank-line separator", () => {
  const result = buildAppendContent("# 旧\n旧正文", "# 新");
  expect(result).toBe("# 旧\n旧正文\n\n# 新");
});

test("buildAppendContent preserves existing content exactly", () => {
  const existing = "# Title\n\nParagraph one.\n\nParagraph two.";
  const result = buildAppendContent(existing, "## New section");
  expect(result).toBe(
    "# Title\n\nParagraph one.\n\nParagraph two.\n\n## New section",
  );
});

test("buildAppendContent works with empty existing (edge case)", () => {
  const result = buildAppendContent("", "# 新");
  expect(result).toBe("\n\n# 新");
});

// ── dispatchFillAction ───────────────────────────────────────────────────

test("dispatchFillAction calls replace handler for replace choice", () => {
  const replace = vi.fn();
  const append = vi.fn();
  dispatchFillAction("replace" as FillChoice, { replace, append });
  expect(replace).toHaveBeenCalledTimes(1);
  expect(append).not.toHaveBeenCalled();
});

test("dispatchFillAction calls append handler for append choice", () => {
  const replace = vi.fn();
  const append = vi.fn();
  dispatchFillAction("append" as FillChoice, { replace, append });
  expect(append).toHaveBeenCalledTimes(1);
  expect(replace).not.toHaveBeenCalled();
});

test("dispatchFillAction calls neither handler for cancel choice", () => {
  const replace = vi.fn();
  const append = vi.fn();
  dispatchFillAction("cancel" as FillChoice, { replace, append });
  expect(replace).not.toHaveBeenCalled();
  expect(append).not.toHaveBeenCalled();
});

// ── shouldForceSnapshotForFill ───────────────────────────────────────────

test("shouldForceSnapshotForFill returns true for replace (destructive)", () => {
  expect(shouldForceSnapshotForFill("replace")).toBe(true);
});

test("shouldForceSnapshotForFill returns false for append (non-destructive)", () => {
  expect(shouldForceSnapshotForFill("append")).toBe(false);
});

test("shouldForceSnapshotForFill returns false for cancel (no-op)", () => {
  expect(shouldForceSnapshotForFill("cancel")).toBe(false);
});
