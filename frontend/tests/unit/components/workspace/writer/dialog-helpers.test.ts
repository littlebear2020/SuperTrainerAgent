/**
 * Dialog helper tests (Task W29 — §9.6).
 *
 * Tests the pure helpers extracted from `dialog-provider.tsx`:
 * - `buildConfirmOptions`: converts a legacy confirm message into structured
 *   `ConfirmOptions` (title + description), with optional overrides.
 * - `buildPromptOptions`: converts a legacy prompt call into structured
 *   `PromptOptions`, with optional overrides.
 *
 * These are pure functions — DOM-free, no React rendering needed.
 */

import { expect, test } from "vitest";

import {
  buildConfirmOptions,
  buildPromptOptions,
} from "@/components/workspace/dialog-provider";

// ── buildConfirmOptions ───────────────────────────────────────────────────

test("buildConfirmOptions uses the message as description with default title", () => {
  const result = buildConfirmOptions(
    "确定清空当前文档全部内容？此操作不可撤销。",
  );
  expect(result).toEqual({
    title: "确认操作",
    description: "确定清空当前文档全部内容？此操作不可撤销。",
    confirmLabel: undefined,
    cancelLabel: undefined,
    variant: undefined,
  });
});

test("buildConfirmOptions allows overriding the title", () => {
  const result = buildConfirmOptions("确定清空当前文档全部内容？", {
    title: "清空文档",
  });
  expect(result.title).toBe("清空文档");
  expect(result.description).toBe("确定清空当前文档全部内容？");
});

test("buildConfirmOptions allows overriding labels and variant", () => {
  const result = buildConfirmOptions("确定删除？", {
    title: "删除文档",
    confirmLabel: "删除",
    cancelLabel: "不了",
    variant: "destructive",
  });
  expect(result).toEqual({
    title: "删除文档",
    description: "确定删除？",
    confirmLabel: "删除",
    cancelLabel: "不了",
    variant: "destructive",
  });
});

test("buildConfirmOptions handles empty message", () => {
  const result = buildConfirmOptions("");
  expect(result.description).toBe("");
  expect(result.title).toBe("确认操作");
});

// ── buildPromptOptions ────────────────────────────────────────────────────

test("buildPromptOptions builds from title + defaultValue", () => {
  const result = buildPromptOptions("插入链接", "https://");
  expect(result).toEqual({
    title: "插入链接",
    label: undefined,
    defaultValue: "https://",
    placeholder: undefined,
  });
});

test("buildPromptOptions uses undefined defaultValue when not provided", () => {
  const result = buildPromptOptions("插入图片");
  expect(result.defaultValue).toBeUndefined();
});

test("buildPromptOptions allows overriding label and placeholder", () => {
  const result = buildPromptOptions("插入公式", "x^2", {
    label: "LaTeX 公式",
    placeholder: "输入公式...",
  });
  expect(result.title).toBe("插入公式");
  expect(result.label).toBe("LaTeX 公式");
  expect(result.defaultValue).toBe("x^2");
  expect(result.placeholder).toBe("输入公式...");
});

test("buildPromptOptions override defaultValue takes precedence", () => {
  const result = buildPromptOptions("插入链接", "https://", {
    defaultValue: "http://override",
  });
  // The explicit second arg takes precedence over overrides.defaultValue
  expect(result.defaultValue).toBe("https://");
});
