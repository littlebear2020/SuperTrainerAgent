import { readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

import {
  showcaseFor,
  type ShowcaseAgent,
  type ShowcaseCase,
} from "@/core/agents/showcase";

const publicRoot = join(process.cwd(), "public");
const agentIds = [
  "course-video",
  "ppt-master",
  "podcast-studio",
  "creative-studio",
] as const;

function agent(id: (typeof agentIds)[number]): ShowcaseAgent {
  const value = showcaseFor(id);
  if (!value) throw new Error(`Missing showcase for ${id}`);
  return value;
}

function filesUnder(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? filesUnder(path) : [path];
  });
}

function caseAssets(item: ShowcaseCase): string[] {
  switch (item.kind) {
    case "video":
      return [item.poster, item.video];
    case "visual":
      return [item.preview, item.original, ...(item.vector ? [item.vector] : [])];
    case "deck":
      return [...item.slides, item.pptx];
    case "podcast":
      return [item.audio];
  }
}

function expectLocalized(value: unknown): void {
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value)) {
    value.forEach(expectLocalized);
    return;
  }
  if ("zh" in value || "en" in value) {
    const localized = value as { zh: unknown; en: unknown };
    for (const entry of [localized.zh, localized.en]) {
      if (Array.isArray(entry)) {
        expect(entry.length).toBeGreaterThan(0);
        for (const line of entry) {
          expect(line).toEqual(expect.any(String));
          expect(line.trim()).not.toBe("");
        }
      } else {
        expect(entry).toEqual(expect.any(String));
        expect(entry.trim()).not.toBe("");
      }
    }
    return;
  }
  Object.values(value).forEach(expectLocalized);
}

describe("Agent showcase data and assets", () => {
  it("resolves only the four showcase agents with the planned case inventory", () => {
    expect(agentIds.map((id) => agent(id).cases.length)).toEqual([3, 3, 4, 10]);
    expect(agentIds.map((id) => agent(id).cases[0]?.kind)).toEqual([
      "video",
      "deck",
      "podcast",
      "visual",
    ]);
    expect(showcaseFor("co-writer")).toBeNull();
    expect(showcaseFor("unknown-agent")).toBeNull();

    for (const id of agentIds) {
      const value = agent(id);
      expect(value.id).toBe(id);
      expect(value.heroLine.zh).toHaveLength(2);
      expect(value.heroLine.en).toHaveLength(2);
      expect(new Set(value.cases.map((item) => item.id)).size).toBe(
        value.cases.length,
      );
      expectLocalized(value);
      for (const item of value.cases) {
        expect(item.prompt.trim()).not.toBe("");
        expect(item.prompt).toMatch(/[一-鿿]/);
      }
    }
  });

  it("keeps every referenced asset present, referenced and within budget", () => {
    const references = new Set(
      agentIds.flatMap((id) => {
        const value = agent(id);
        return [value.character, ...value.cases.flatMap(caseAssets)];
      }),
    );
    const assetRoot = join(publicRoot, "agent-showcase");
    const actual = filesUnder(assetRoot)
      .filter((path) => !path.endsWith("README.md"))
      .map((path) => {
        const url = `/${relative(publicRoot, path).split("\\").join("/")}`;
        const reference = references.has(url);
        expect(reference, `Unreferenced asset: ${url}`).toBe(true);
        return url;
      })
      .sort();

    expect([...references].sort()).toEqual(actual);
    expect(references.size).toBe(58);
    expect(actual.filter((path) => path.startsWith("/agent-showcase/hero/"))).toHaveLength(4);
    expect(actual.filter((path) => path.endsWith(".mp3"))).toHaveLength(4);
    expect(
      actual.filter((path) =>
        path.startsWith("/agent-showcase/visual/previews/"),
      ),
    ).toHaveLength(10);
    expect(
      actual.filter((path) =>
        path.startsWith("/agent-showcase/ppt/previews/"),
      ),
    ).toHaveLength(21);

    let totalBytes = 0;
    for (const url of references) {
      expect(url).toMatch(/^\/agent-showcase\//);
      expect(url).not.toContain("..");
      expect(url).not.toMatch(/data:|base64|bundle\.html/i);
      totalBytes += statSync(join(publicRoot, url)).size;
    }
    expect(totalBytes).toBeLessThanOrEqual(20_000_000);

    for (const item of agent("ppt-master").cases) {
      if (item.kind !== "deck") throw new Error("Expected deck cases");
      expect(item.slides).toHaveLength(7);
      expect(item.pageCount).toBe(7);
      expect(statSync(join(publicRoot, item.pptx)).size).toBe(item.pptxBytes);
    }
  });

  it("preserves the agent workflow markers and confirmation boundaries", () => {
    for (const item of agent("course-video").cases) {
      expect(item.prompt).toContain("auto-video-flow");
      expect(item.prompt).toMatch(/确认/);
    }
    expect(agent("course-video").cases[0]?.prompt).toContain("严格依据我上传");
    expect(agent("course-video").cases[1]?.prompt).toContain("严格依据我上传");
    expect(agent("course-video").cases[2]?.prompt).toContain("来源");

    const deckPrompts = agent("ppt-master").cases.map((item) => item.prompt);
    expect(deckPrompts[0]).toContain("Quick");
    expect(deckPrompts[0]).toContain("Stage 1/2");
    expect(deckPrompts[1]).toContain("Default");
    expect(deckPrompts[1]).toMatch(/先.*确认.*结构.*方案/);
    expect(deckPrompts[2]).toContain("Edit Native");
    expect(deckPrompts[2]).toMatch(/保留原.*设计/);
    expect(deckPrompts[2]).toMatch(/未编辑.*不重建|未修改的对象保持原样/);

    expect(
      agent("podcast-studio").cases.map((item) => item.prompt.split("\n")[0]),
    ).toEqual([
      "[模式: read_original]",
      "[模式: news_single]",
      "[模式: interview_two]",
      "[模式: debate_two]",
    ]);

    for (const item of agent("creative-studio").cases) {
      if (item.kind !== "visual") throw new Error("Expected visual cases");
      expect(item.prompt.split("\n")[0]).toBe(
        item.category === "diagram" ? "[模式: 结构图]" : "[模式: 视觉创作]",
      );
    }
  });
});
