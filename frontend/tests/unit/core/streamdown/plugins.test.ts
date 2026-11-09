import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import { expect, test } from "vitest";

import {
  rehypeClobberFragments,
  reasoningPlugins,
  streamdownSanitizeSchema,
  streamdownPlugins,
} from "@/core/streamdown/plugins";

test("streamdownPlugins includes rehypeRaw", () => {
  expect(streamdownPlugins.rehypePlugins).toContain(rehypeRaw);
});

test("reasoningPlugins does not include rehypeRaw", () => {
  const flat = reasoningPlugins.rehypePlugins?.flat();
  expect(flat).not.toContain(rehypeRaw);
});

// SEC-H1: 带 rehypeRaw 的插件集必须在 raw → sanitize → katex 顺序下消毒——
// sanitize 晚于 raw（剥掉原始 HTML 向量）、早于 katex（KaTeX 产物不受 schema 约束）。
test("streamdownPlugins sanitizes between rehypeRaw and rehypeKatex (SEC-H1)", () => {
  const plugins = streamdownPlugins.rehypePlugins ?? [];
  const sanitizeEntry = plugins.find(
    (p) => Array.isArray(p) && p[0] === rehypeSanitize,
  );
  expect(sanitizeEntry).toBeTruthy();

  const indexOf = (target: unknown) =>
    plugins.findIndex((p) =>
      Array.isArray(p) ? p[0] === target : p === target,
    );
  const rawIdx = indexOf(rehypeRaw);
  const sanitizeIdx = indexOf(rehypeSanitize);
  const katexIdx = indexOf(rehypeKatex);
  expect(rawIdx).toBeGreaterThanOrEqual(0);
  expect(sanitizeIdx).toBeGreaterThan(rawIdx);
  expect(katexIdx).toBeGreaterThan(sanitizeIdx);
});

// SEC-H1 纵深：reasoningPlugins 滤掉 rehypeRaw 时须保留 sanitize。
test("reasoningPlugins keeps rehypeSanitize after filtering out rehypeRaw", () => {
  const flat = reasoningPlugins.rehypePlugins?.flat();
  expect(flat).toContain(rehypeSanitize);
});

// PR-2 3.3（D1 合流）实证四则：

// ① schema 白名单含 math class——KaTeX 在 sanitize 之后运行的前提：remark-math
// 把公式标成 code.language-math.math-inline|display，sanitize 不放行就剥掉标记。
test("streamdownSanitizeSchema allows math classes on code (D1 merge)", () => {
  const codeAttrs = streamdownSanitizeSchema.attributes?.code ?? [];
  const classNameEntry = codeAttrs.find(
    (entry) => Array.isArray(entry) && entry[0] === "className",
  ) as unknown[] | undefined;
  expect(classNameEntry).toBeTruthy();
  expect(classNameEntry).toEqual(
    expect.arrayContaining(["math-inline", "math-display"]),
  );
});

// ② 本地 SEC-H1 扩展随合流保留（D1：本地为准）。
test("streamdownSanitizeSchema keeps SEC-H1 local extensions", () => {
  expect(streamdownSanitizeSchema.attributes?.["*"]).toContain("className");
  const spanAttrs = streamdownSanitizeSchema.attributes?.span ?? [];
  expect(spanAttrs).toContain("data-rough-notation");
  expect(spanAttrs).toContain("dataRoughNotation");
});

// ③ clobber 前缀片段翻译紧跟 sanitize 步（脚注双前缀归一的次序前提）。
test("rehypeClobberFragments sits right after the sanitize step", () => {
  const plugins = streamdownPlugins.rehypePlugins ?? [];
  const indexOf = (target: unknown) =>
    plugins.findIndex((p) =>
      Array.isArray(p) ? p[0] === target : p === target,
    );
  expect(indexOf(rehypeClobberFragments)).toBe(indexOf(rehypeSanitize) + 1);
});

// ④ 渲染插件集（shiki code / mermaid）进 preset——插件化可裁剪的接线位。
test("streamdownPlugins carries code and mermaid rendering plugins", () => {
  expect(streamdownPlugins.plugins).toBeTruthy();
  expect(Object.keys(streamdownPlugins.plugins ?? {})).toEqual(
    expect.arrayContaining(["code", "mermaid"]),
  );
});
