/**
 * B3-T4: extractMarkdownFromAiOutput tests.
 *
 * The bug: handleFullDraftApply detected JSON-shaped output and immediately
 * toast.error'd + returned -- user waited tens of seconds for nothing.
 * Fix: try to unwrap the JSON and extract the content field.
 */

import { expect, test } from "vitest";

import { extractMarkdownFromAiOutput } from "@/core/writer/extract-markdown";

test("extracts content from {content: ...} JSON wrapper", () => {
  const raw = JSON.stringify({ content: "# 标题\n\n正文内容" });
  expect(extractMarkdownFromAiOutput(raw)).toBe("# 标题\n\n正文内容");
});

test("extracts markdown from {markdown: ...} JSON wrapper", () => {
  const raw = JSON.stringify({ markdown: "## 副标题" });
  expect(extractMarkdownFromAiOutput(raw)).toBe("## 副标题");
});

test("extracts text from {text: ...} JSON wrapper", () => {
  const raw = JSON.stringify({ text: "纯文本内容" });
  expect(extractMarkdownFromAiOutput(raw)).toBe("纯文本内容");
});

test("extracts result from {result: ...} JSON wrapper", () => {
  const raw = JSON.stringify({ result: "# AI 生成结果" });
  expect(extractMarkdownFromAiOutput(raw)).toBe("# AI 生成结果");
});

test("extracts draft from {draft: ...} JSON wrapper", () => {
  const raw = JSON.stringify({ draft: "草稿内容" });
  expect(extractMarkdownFromAiOutput(raw)).toBe("草稿内容");
});

test("returns null for valid JSON without known content fields", () => {
  const raw = JSON.stringify({ status: "ok", count: 42 });
  expect(extractMarkdownFromAiOutput(raw)).toBeNull();
});

test("returns null for JSON with non-string content field", () => {
  const raw = JSON.stringify({ content: 12345 });
  expect(extractMarkdownFromAiOutput(raw)).toBeNull();
});

test("returns null for JSON with empty string content", () => {
  const raw = JSON.stringify({ content: "" });
  expect(extractMarkdownFromAiOutput(raw)).toBeNull();
});

test("passes through non-JSON markdown unchanged", () => {
  const md = "# 标题\n\n这是正常的 Markdown 内容";
  expect(extractMarkdownFromAiOutput(md)).toBe(md);
});

test("passes through markdown starting with { that isn't valid JSON", () => {
  // e.g. a math formula or template literal
  const md = "{这是一个伪 JSON 内容}";
  expect(extractMarkdownFromAiOutput(md)).toBe(md);
});

test("passes through markdown starting with [ that isn't valid JSON", () => {
  const md = "[注意] 这是一个列表项";
  expect(extractMarkdownFromAiOutput(md)).toBe(md);
});
