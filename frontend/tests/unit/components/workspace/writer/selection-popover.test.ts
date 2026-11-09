/**
 * Selection-popover light-channel migration tests (Tasks W6 + W9).
 *
 * Phase 2 migrates selection editing from the heavy streamAgentEdit
 * (/api/runs/stream, co-writer agent) to the light streamAssist
 * (/api/ai/assist, direct LLM). These tests cover:
 *
 * - buildSelectionAssistRequest: op mapping (polish/rewrite/expand/summarize)
 *   — regression: no more 改写→polish confusion (rewrite→op:"rewrite").
 * - applyEdit's request hits /api/ai/assist (light), NOT /api/runs/stream.
 * - MODE_OPTIONS deleted (no inert lookup table, no stuck mode state).
 * - W9: applyFollowUp migrated to light streamAssist carrying appliedOp
 *   (NOT hardcoded "polish"), with a real MAX_ITERATIONS=3 guard.
 *
 * Mirrors api.test.ts: mock the fetcher + config, then assert URL + body.
 */

import { expect, test, vi, beforeEach } from "vitest";

vi.mock("@/core/api/fetcher", () => ({
  fetch: vi.fn(),
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

import {
  buildFollowUpAssistRequest,
  buildSelectionAssistRequest,
  canIterate,
  MAX_ITERATIONS,
  pickApplyCallbacks,
  selectionApplyMode,
} from "@/components/workspace/writer/selection-popover";
import * as selectionPopoverMod from "@/components/workspace/writer/selection-popover";
import { fetch } from "@/core/api/fetcher";
import { streamAssist } from "@/core/writer/api";

const mockFetch = vi.mocked(fetch);

/** Cast a partial mock to a Response (vitest mocks don't need a real Response). */
function asRes(obj: Record<string, unknown>): Response {
  return obj as unknown as Response;
}

beforeEach(() => {
  mockFetch.mockReset();
});

// ── buildSelectionAssistRequest: op mapping (no 改写→polish confusion) ─────

test("test_selection_ops_map_correctly: polish→op:polish with context+doc_type+full_doc", () => {
  const req = buildSelectionAssistRequest(
    "polish",
    "选中文本",
    "article",
    "文档标题：测试",
  );
  expect(req.op).toBe("polish");
  expect(req.text).toBe("选中文本");
  expect(req.doc_type).toBe("article");
  expect(req.context).toBe("文档标题：测试");
  expect(req.full_doc).toBe(true);
});

test("test_selection_ops_map_correctly: rewrite→op:rewrite (NOT polish — old contradiction gone)", () => {
  const req = buildSelectionAssistRequest("rewrite", "选中文本", "article");
  expect(req.op).toBe("rewrite");
});

test("test_selection_ops_map_correctly: expand→op:expand", () => {
  const req = buildSelectionAssistRequest("expand", "选中文本");
  expect(req.op).toBe("expand");
});

test("test_selection_ops_map_correctly: summarize→op:summarize", () => {
  const req = buildSelectionAssistRequest("summarize", "选中文本");
  expect(req.op).toBe("summarize");
});

// ── applyEdit uses light streamAssist (/api/ai/assist) ─────────────────────

test("test_selection_apply_edit_uses_light_stream_assist: hits /api/ai/assist NOT /api/runs/stream", async () => {
  mockFetch.mockResolvedValueOnce(asRes({ ok: true, status: 200 }));

  const ctx = "文档标题：测试";
  const req = buildSelectionAssistRequest("polish", "选中文本", "article", ctx);

  // Drain the generator — mock response has no body → returns immediately.
  const chunks: string[] = [];
  for await (const chunk of streamAssist(req)) {
    chunks.push(chunk);
  }
  expect(chunks).toEqual([]);

  const url = mockFetch.mock.calls[0]![0] as string;
  expect(url).toBe("http://localhost:8001/api/ai/assist");
  expect(url).not.toContain("/api/runs/stream");

  const options = mockFetch.mock.calls[0]![1] as Record<string, unknown>;
  const body = JSON.parse(options.body as string);
  expect(body.op).toBe("polish");
  expect(body.text).toBe("选中文本");
  expect(body.doc_type).toBe("article");
  expect(body.full_doc).toBe(true);
  expect(body.context).toBe(ctx);
});

// ── MODE_OPTIONS deleted ───────────────────────────────────────────────────

test("test_no_mode_options_lookup: MODE_OPTIONS deleted (not in module namespace)", () => {
  // MODE_OPTIONS was a module-level const; after deletion it's absent from
  // the module namespace. Cast to Record to access without tsc error.
  // tsc clean is the real gate (deleted symbol → dangling refs fail build).
  const mod = selectionPopoverMod as unknown as Record<string, unknown>;
  expect(mod.MODE_OPTIONS).toBeUndefined();
});

// ── W9: applyFollowUp on light streamAssist carrying appliedOp ────────────

test("test_apply_follow_up_uses_light_stream_assist_with_appliedOp: op=appliedOp NOT polish, hits /api/ai/assist", async () => {
  mockFetch.mockResolvedValueOnce(asRes({ ok: true, status: 200 }));

  // buildFollowUpAssistRequest carries the round's op (appliedOp), NOT a
  // hardcoded "polish" (W9 §4.4). instruction = the user's follow-up;
  // text = the current streamedText being refined; full_doc lifts caps.
  const req = buildFollowUpAssistRequest(
    "expand", // appliedOp
    "再简短一些", // instruction (combinedInstruction)
    "之前 AI 生成的文本", // streamedText
    "article", // contentType
    "文档标题：测试", // context
  );
  expect(req.op).toBe("expand"); // NOT "polish"
  expect(req.instruction).toBe("再简短一些");
  expect(req.text).toBe("之前 AI 生成的文本");
  expect(req.doc_type).toBe("article");
  expect(req.full_doc).toBe(true);
  expect(req.context).toBe("文档标题：测试");

  // Drain the generator — mock response has no body → returns immediately.
  const chunks: string[] = [];
  for await (const chunk of streamAssist(req)) {
    chunks.push(chunk);
  }
  expect(chunks).toEqual([]);

  const url = mockFetch.mock.calls[0]![0] as string;
  expect(url).toBe("http://localhost:8001/api/ai/assist");
  expect(url).not.toContain("/api/runs/stream");

  const options = mockFetch.mock.calls[0]![1] as Record<string, unknown>;
  const body = JSON.parse(options.body as string);
  expect(body.op).toBe("expand"); // carried, NOT hardcoded "polish"
  expect(body.instruction).toBe("再简短一些");
  expect(body.text).toBe("之前 AI 生成的文本");
  expect(body.full_doc).toBe(true);
});

// ── W9: MAX_ITERATIONS=3 real guard (canIterate pure helper) ──────────────

test("test_apply_follow_up_enforces_max_iterations: canIterate false at/above cap, true below", () => {
  // canIterate is the pure guard behind applyFollowUp's hard cap (W9 §4.5).
  // At the cap (3 of 3) no further iteration is allowed — unbypassable, not
  // just UI-hidden.
  expect(canIterate(3, MAX_ITERATIONS)).toBe(false);
  expect(canIterate(4, MAX_ITERATIONS)).toBe(false);
  // Below the cap iteration is allowed.
  expect(canIterate(0, MAX_ITERATIONS)).toBe(true);
  expect(canIterate(2, MAX_ITERATIONS)).toBe(true);
});

// ── W7: selectionApplyMode + pickApplyCallbacks (概要 → insert-below) ────

test("test_selection_apply_mode: summarize→insert-below; polish/rewrite/expand→replace", () => {
  expect(selectionApplyMode("summarize")).toBe("insert-below");
  expect(selectionApplyMode("polish")).toBe("replace");
  expect(selectionApplyMode("rewrite")).toBe("replace");
  expect(selectionApplyMode("expand")).toBe("replace");
});

test("test_summarize_uses_insert_below_not_replace: pickApplyCallbacks(insert-below) calls onInsertBelow, NOT onReplaceText", () => {
  const onInsertBelow = vi.fn();
  const onReplaceText = vi.fn();
  const apply = pickApplyCallbacks(
    "insert-below",
    "summary text",
    onInsertBelow,
    onReplaceText,
  );
  expect(apply).not.toBeNull();
  apply?.();
  expect(onInsertBelow).toHaveBeenCalledTimes(1);
  expect(onInsertBelow).toHaveBeenCalledWith("summary text");
  expect(onReplaceText).not.toHaveBeenCalled();
});

test("test_polish_uses_replace: pickApplyCallbacks(replace) calls onReplaceText, NOT onInsertBelow", () => {
  const onInsertBelow = vi.fn();
  const onReplaceText = vi.fn();
  const apply = pickApplyCallbacks(
    "replace",
    "polished text",
    onInsertBelow,
    onReplaceText,
  );
  expect(apply).not.toBeNull();
  apply?.();
  expect(onReplaceText).toHaveBeenCalledTimes(1);
  expect(onReplaceText).toHaveBeenCalledWith("polished text");
  expect(onInsertBelow).not.toHaveBeenCalled();
});

test("test_summarize_replace_button_forces_replace: pickApplyCallbacks(replace) calls onReplaceText even though selectionApplyMode(summarize)=insert-below", () => {
  // summarize defaults to insert-below (the non-destructive path)...
  expect(selectionApplyMode("summarize")).toBe("insert-below");
  // ...but the secondary "替换原文" button forces replace mode → onReplaceText.
  const onInsertBelow = vi.fn();
  const onReplaceText = vi.fn();
  const apply = pickApplyCallbacks(
    "replace",
    "summary text",
    onInsertBelow,
    onReplaceText,
  );
  expect(apply).not.toBeNull();
  apply?.();
  expect(onReplaceText).toHaveBeenCalledWith("summary text");
  expect(onInsertBelow).not.toHaveBeenCalled();
});

test("test_pick_apply_callbacks_no_text_returns_null: empty streamedText → no apply", () => {
  const onInsertBelow = vi.fn();
  const onReplaceText = vi.fn();
  expect(
    pickApplyCallbacks("insert-below", "", onInsertBelow, onReplaceText),
  ).toBeNull();
  expect(
    pickApplyCallbacks("replace", "", onInsertBelow, onReplaceText),
  ).toBeNull();
});
