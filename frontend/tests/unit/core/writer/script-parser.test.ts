/**
 * script-parser tests.
 *
 * Pins the real contract of parseScriptScenes / stripMarkdownForTts /
 * scenesToNarrationText / totalEstimatedDuration as implemented in
 * src/core/writer/script-parser.ts:
 *
 * - Scenes are delimited by `## ` headings; preamble before the first
 *   heading is skipped. `### ` and `##` without a space are NOT delimiters.
 * - `**画面**：` / `**解说词**：` (plus synonyms, fullwidth or ascii colon)
 *   mark visual vs narration. Narration is markdown-stripped for TTS;
 *   visual is kept raw.
 * - Unlabeled lines attach to the visual block while one is open and no
 *   narration has started; otherwise they collect as fallback narration,
 *   used ONLY when no explicit narration text exists (trailing extras are
 *   dropped) — narrower than the "TTS never loses content" doc comment.
 * - estimatedDuration = max(1, ceil(narrationChars / 3)).
 */

import { describe, expect, test } from "vitest";

import {
  parseScriptScenes,
  scenesToNarrationText,
  stripMarkdownForTts,
  totalEstimatedDuration,
  type ScriptScene,
} from "@/core/writer/script-parser";

const makeScene = (overrides: Partial<ScriptScene>): ScriptScene => ({
  index: 0,
  title: "场景",
  visual: null,
  narration: "",
  estimatedDuration: 1,
  ...overrides,
});

describe("parseScriptScenes", () => {
  test("parses the documented template into a scene", () => {
    const md = [
      "# 视频标题",
      "",
      "## 场景 1",
      "",
      "**画面**：日落的海边",
      "",
      "**解说词**：大家好欢迎来到本期视频",
    ].join("\n");
    expect(parseScriptScenes(md)).toEqual([
      {
        index: 0,
        title: "场景 1",
        visual: "日落的海边",
        narration: "大家好欢迎来到本期视频",
        estimatedDuration: 4, // ceil(10 chars / 3)
      },
    ]);
  });

  test("parses multiple scenes with zero-based indexes and trimmed titles", () => {
    const md = [
      "##   开场  ",
      "**解说词**：第一段",
      "## 结尾",
      "**解说词**：第二段",
    ].join("\n");
    const scenes = parseScriptScenes(md);
    expect(scenes.map((s) => [s.index, s.title, s.narration])).toEqual([
      [0, "开场", "第一段"],
      [1, "结尾", "第二段"],
    ]);
  });

  test("returns [] for empty, whitespace-only, or nullish input", () => {
    expect(parseScriptScenes("")).toEqual([]);
    expect(parseScriptScenes("   \n \n  ")).toEqual([]);
    expect(parseScriptScenes(null as unknown as string)).toEqual([]);
  });

  test("returns [] when the document has no ## scene headings", () => {
    expect(parseScriptScenes("# 标题\n\n一段正文")).toEqual([]);
  });

  test("skips preamble before the first scene heading", () => {
    const md = "# 视频标题\n作者：张三\n\n## 场景 1\n**解说词**：正文";
    const scenes = parseScriptScenes(md);
    expect(scenes).toHaveLength(1);
    expect(scenes[0]?.narration).toBe("正文");
  });

  test("accepts label synonyms and ascii colons", () => {
    const md = [
      "## 场景 1",
      "**视觉**: 灯塔远景",
      "**旁白**: 第一句",
      "## 场景 2",
      "**画面描述**：特写",
      "**台词**：第二句",
    ].join("\n");
    expect(parseScriptScenes(md)).toEqual([
      {
        index: 0,
        title: "场景 1",
        visual: "灯塔远景",
        narration: "第一句",
        estimatedDuration: 1,
      },
      {
        index: 1,
        title: "场景 2",
        visual: "特写",
        narration: "第二句",
        estimatedDuration: 1,
      },
    ]);
  });

  test("appends unlabeled lines to the visual block until narration starts", () => {
    const md = [
      "## 场景 1",
      "**画面**：第一行画面",
      "第二行画面",
      "**解说词**：解说",
    ].join("\n");
    const scenes = parseScriptScenes(md);
    expect(scenes[0]?.visual).toBe("第一行画面\n第二行画面");
    expect(scenes[0]?.narration).toBe("解说");
  });

  test("uses unlabeled lines as fallback narration when no narration label exists", () => {
    const md = "## 场景 1\n第一段口播\n第二段口播";
    const scenes = parseScriptScenes(md);
    expect(scenes[0]?.visual).toBeNull();
    expect(scenes[0]?.narration).toBe("第一段口播\n第二段口播");
  });

  test("drops unlabeled trailing lines when explicit narration exists", () => {
    // The doc comment says unmatched text is "appended to narration"; in
    // reality fallback lines are used only when narration is empty.
    const md = "## 场景 1\n**解说词**：正式解说\n花絮文字";
    expect(parseScriptScenes(md)[0]?.narration).toBe("正式解说");
  });

  test("treats an empty 画面 label as an empty-string visual, not null", () => {
    const md = "## 场景 1\n**画面**：\n**解说词**：解说";
    expect(parseScriptScenes(md)[0]?.visual).toBe("");
  });

  test("strips markdown from narration but keeps visual raw", () => {
    const md = [
      "## 场景 1",
      "**画面**：一段**加粗**的画面",
      "**解说词**：这是**重点**内容",
    ].join("\n");
    const scenes = parseScriptScenes(md);
    expect(scenes[0]?.visual).toBe("一段**加粗**的画面");
    expect(scenes[0]?.narration).toBe("这是重点内容");
  });

  test("estimates duration at ~3 chars/s with a 1s floor", () => {
    const md = [
      "## 场景 1",
      "**解说词**：123456789", // 9 chars → 3s
      "## 场景 2",
      "**解说词**：1234567890", // 10 chars → ceil → 4s
      "## 场景 3",
      "**画面**：无解说", // empty narration → floor of 1s
    ].join("\n");
    expect(parseScriptScenes(md).map((s) => s.estimatedDuration)).toEqual([
      3, 4, 1,
    ]);
  });

  test("does not treat ### headings as scene delimiters", () => {
    const md = "## 场景 1\n### 子标题";
    const scenes = parseScriptScenes(md);
    expect(scenes).toHaveLength(1);
    // The ### line lands in fallback narration; TTS stripping drops the marker.
    expect(scenes[0]?.narration).toBe("子标题");
  });

  test("does not treat ## without a trailing space as a scene delimiter", () => {
    expect(parseScriptScenes("##标题\n**解说词**：正文")).toEqual([]);
  });

  test("parses CRLF input as zero scenes (pinned current behavior)", () => {
    // `.` in the heading/label regexes excludes \r, so CRLF lines never
    // match — arguably a bug, pinned to document current behavior.
    expect(parseScriptScenes("## 场景 1\r\n**解说词**：正文\r\n")).toEqual([]);
  });
});

describe("stripMarkdownForTts", () => {
  test("strips headings, emphasis, inline code, links, quotes, and list markers", () => {
    const md =
      "# 标题\n**加粗**和*斜体*加`代码`\n> 引用\n- 列表项\n[链接文字](https://a.b)";
    expect(stripMarkdownForTts(md)).toBe(
      "标题\n加粗和斜体加代码\n引用\n列表项\n链接文字",
    );
  });

  test("returns empty string for empty input", () => {
    expect(stripMarkdownForTts("")).toBe("");
  });

  test("removes fenced code blocks entirely (2026-10-06 合并批修前缺陷)", () => {
    // 修前（pinned）：inline-code 规则先于 fence 规则把围栏背号啃成 ``\ncode\n``，
    // 朗读会读出残背号。规则表收敛到 text-utils 后 fence 整删（TTS 替换符 ""）。
    expect(stripMarkdownForTts("```\ncode\n```")).toBe("");
  });

  test("extracts image alt text (2026-10-06 合并批修前缺陷)", () => {
    // 修前（pinned）：不处理图片语法，残留 "!alt" 的感叹号进朗读。
    expect(stripMarkdownForTts("![alt](img.png)")).toBe("alt");
  });

  test("does not strip HTML tags (documented as backend's job)", () => {
    expect(stripMarkdownForTts("<p>你好</p>")).toBe("<p>你好</p>");
  });
});

describe("scenesToNarrationText", () => {
  test("joins narrations with a blank line and skips empty ones", () => {
    const scenes = [
      makeScene({ narration: "第一段" }),
      makeScene({ index: 1, narration: "" }),
      makeScene({ index: 2, narration: "第三段" }),
    ];
    expect(scenesToNarrationText(scenes)).toBe("第一段\n\n第三段");
  });

  test("returns empty string for no scenes", () => {
    expect(scenesToNarrationText([])).toBe("");
  });
});

describe("totalEstimatedDuration", () => {
  test("sums scene durations", () => {
    const scenes = [
      makeScene({ estimatedDuration: 3 }),
      makeScene({ index: 1, estimatedDuration: 4 }),
    ];
    expect(totalEstimatedDuration(scenes)).toBe(7);
  });

  test("returns 0 for no scenes", () => {
    expect(totalEstimatedDuration([])).toBe(0);
  });
});
