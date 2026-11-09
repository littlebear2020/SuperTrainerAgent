import { expect, test } from "vitest";

import {
  isFullDocHeuristic,
  parseStageSignal,
} from "@/core/writer/from-scratch-stage";
import { parseScratchSSE } from "@/core/writer/use-from-scratch";

// ── Helpers ──────────────────────────────────────────────────────────────

/** Encode a string to Uint8Array. */
function encode(str: string): Uint8Array {
  return new TextEncoder().encode(str);
}

/** Create a ReadableStream that emits chunks then closes. */
function makeStream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
}

/** Build an SSE frame string. */
function sseFrame(event: string, data: string): string {
  return `event: ${event}\ndata: ${data}\n\n`;
}

// ── parseStageSignal (the pure helper) ───────────────────────────────────

test("parseStageSignal_outline detects and strips the OUTLINE marker", () => {
  const input = "[[STAGE:OUTLINE]]\n\n## 一、背景\n背景内容";
  const result = parseStageSignal(input);

  expect(result.stage).toBe("outline");
  expect(result.text).toBe("\n\n## 一、背景\n背景内容");
  // Marker must be stripped from text
  expect(result.text).not.toContain("[[STAGE:");
});

test("parseStageSignal_draft detects and strips the DRAFT marker", () => {
  const input = "[[STAGE:DRAFT]]\n\n# 标题\n全文正文";
  const result = parseStageSignal(input);

  expect(result.stage).toBe("draft");
  expect(result.text).toBe("\n\n# 标题\n全文正文");
  expect(result.text).not.toContain("[[STAGE:");
});

test("parseStageSignal_null_when_marker_absent (fallback path)", () => {
  const input = "## 大纲\n一、背景";
  const result = parseStageSignal(input);

  expect(result.stage).toBeNull();
  // text returned unchanged when no marker
  expect(result.text).toBe(input);
});

test("parseStageSignal does not match marker embedded mid-text", () => {
  const input = "正文内容\n[[STAGE:OUTLINE]]\n更多正文";
  const result = parseStageSignal(input);

  expect(result.stage).toBeNull();
  expect(result.text).toBe(input);
});

// ── buildResult (via parseScratchSSE) uses signal when present ────────────

test("buildResult_uses_signal_when_present: OUTLINE → isOutline, not isFullDoc", async () => {
  const outlineContent =
    "[[STAGE:OUTLINE]]\n\n## 一、背景\n## 二、目标\n## 三、方法";
  const msg = JSON.stringify([{ id: "msg-1", content: outlineContent }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  // When signal present, use it: outline → isOutline=true, NOT isFullDoc
  expect(result!.isOutline).toBe(true);
  expect(result!.isFullDoc).toBe(false);
  expect(result!.isClarification).toBe(false);
  // Marker stripped from displayed text
  expect(result!.text).not.toContain("[[STAGE:");
  expect(result!.text).toContain("背景");
});

test("buildResult_uses_signal_when_present: DRAFT → isFullDoc", async () => {
  const draftContent = "[[STAGE:DRAFT]]\n\n# 标题\n" + "正文内容".repeat(20);
  const msg = JSON.stringify([{ id: "msg-1", content: draftContent }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  // draft signal → isFullDoc=true, isOutline=false
  expect(result!.isFullDoc).toBe(true);
  expect(result!.isOutline).toBe(false);
  expect(result!.text).not.toContain("[[STAGE:");
});

test("buildResult_falls_back_when_signal_absent (old heuristic intact)", async () => {
  // No stage marker: >200 chars, heading, no "请问" → isFullDoc=true (old behavior)
  const longText = "# 第一章 微服务入门\n\n".padEnd(250, "正文内容");
  const msg = JSON.stringify([{ id: "msg-1", content: longText }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  // Fallback: old heuristic applies
  expect(result!.isFullDoc).toBe(true);
  expect(result!.isOutline).toBe(false);
  // text unchanged (no marker to strip)
  expect(result!.text).toBe(longText);
});

test("buildResult_falls_back: question keyword still suppresses isFullDoc", async () => {
  // No marker + "请问" → isFullDoc=false (old heuristic's !includes guard)
  const longText = "# 大纲\n\n".padEnd(250, "内容") + "请问这个大纲可以吗？";
  const msg = JSON.stringify([{ id: "msg-1", content: longText }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  expect(result!.isFullDoc).toBe(false);
  expect(result!.isOutline).toBe(false);
});

// ── Regression: signal OVERRIDES the legacy heuristic (review #1) ─────────

test("buildResult_signal_overrides_heuristic: long OUTLINE → isOutline, NOT isFullDoc (review #1)", async () => {
  // The user's #1 pain: a long outline (>200 chars of headings, no "请问") that
  // the legacy heuristic alone would classify as a full doc (isFullDoc=true).
  // The [[STAGE:OUTLINE]] signal must override the heuristic so the panel shows
  // the 📋 outline hint (not the 📄 draft hint). buildResult's signal path is
  // correct; this pins it as a regression guard for the contract the panel now
  // reads via message.isOutline / message.isFullDoc.
  const headings = Array.from(
    { length: 20 },
    (_, i) => `## 第${i + 1}章 这是一个较长的章节标题`,
  ).join("\n");
  const outlineContent = `[[STAGE:OUTLINE]]\n\n${headings}`;
  const msg = JSON.stringify([{ id: "msg-1", content: outlineContent }]);
  const stream = makeStream([
    encode(sseFrame("messages", msg)),
    encode(sseFrame("end", "{}")),
  ]);

  const result = await parseScratchSSE(stream);

  expect(result).not.toBeNull();
  // Sanity: the heuristic alone WOULD say isFullDoc (without the signal this is
  // misclassified as a draft) — this is what makes the regression meaningful.
  expect(isFullDocHeuristic(result!.text)).toBe(true);
  // Signal overrides the heuristic:
  expect(result!.isOutline).toBe(true);
  expect(result!.isFullDoc).toBe(false);
  expect(result!.isClarification).toBe(false);
  // Marker stripped from the threaded text
  expect(result!.text).not.toContain("[[STAGE:");
  expect(result!.text).toContain("第1章");
});
