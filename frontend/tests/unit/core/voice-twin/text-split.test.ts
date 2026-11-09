import { expect, test } from "vitest";

import { splitTextToSegments } from "@/core/voice-twin/text-split";

test("returns empty array for empty string", () => {
  expect(splitTextToSegments("")).toEqual([]);
});

test("returns empty array for whitespace only", () => {
  expect(splitTextToSegments("   \n  ")).toEqual([]);
});

test("returns single segment for short text without punctuation", () => {
  expect(splitTextToSegments("Hello world")).toEqual(["Hello world"]);
});

test("splits on Chinese period", () => {
  const result = splitTextToSegments("第一句话。第二句话。第三句话。");
  expect(result).toEqual(["第一句话。", "第二句话。", "第三句话。"]);
});

test("splits on Chinese exclamation and question marks", () => {
  const result = splitTextToSegments("你好！怎么样？很好！");
  expect(result).toEqual(["你好！", "怎么样？", "很好！"]);
});

test("splits on English sentence punctuation", () => {
  const result = splitTextToSegments("First. Second? Third!");
  expect(result).toEqual(["First.", "Second?", "Third!"]);
});

test("splits on double newlines (paragraph breaks)", () => {
  const result = splitTextToSegments("段落一\n\n段落二\n\n段落三");
  expect(result).toEqual(["段落一", "段落二", "段落三"]);
});

test("splits on single newlines", () => {
  const result = splitTextToSegments("第一行\n第二行\n第三行");
  expect(result).toEqual(["第一行", "第二行", "第三行"]);
});

test("hard-splits segments exceeding 500 chars", () => {
  const longText = "很长的文本".repeat(200); // 1000 chars, no punctuation
  const result = splitTextToSegments(longText);
  expect(result.length).toBeGreaterThan(1);
  for (const seg of result) {
    expect(seg.length).toBeLessThanOrEqual(500);
  }
  expect(result.join("")).toBe(longText);
});

test("preserves content when joined", () => {
  const text = "这是第一段。\n\n这是第二段，包含更多内容！还有疑问？";
  const result = splitTextToSegments(text);
  const joined = result.join("");
  expect(joined).toContain("这是第一段。");
  expect(joined).toContain("这是第二段，包含更多内容！还有疑问？");
});

test("handles mixed Chinese and English punctuation", () => {
  const result = splitTextToSegments("Hello world。This is mixed！Really?");
  expect(result.length).toBeGreaterThan(1);
});

test("trims whitespace from segments after split", () => {
  const result = splitTextToSegments("First sentence.  Second sentence.");
  expect(result).toEqual(["First sentence.", "Second sentence."]);
});
