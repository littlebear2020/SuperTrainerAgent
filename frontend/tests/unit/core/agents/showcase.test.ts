import { readdirSync, readFileSync, statSync } from "node:fs";
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
      return [
        item.preview,
        item.original,
        ...(item.vector ? [item.vector] : []),
      ];
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
        if (typeof entry !== "string") {
          throw new Error("Localized text must be a string or string array");
        }
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
    expect(
      actual.filter((path) => path.startsWith("/agent-showcase/hero/")),
    ).toHaveLength(4);
    expect(actual.filter((path) => path.endsWith(".mp3"))).toHaveLength(4);
    expect(
      actual.filter((path) =>
        path.startsWith("/agent-showcase/visual/previews/"),
      ),
    ).toHaveLength(10);
    expect(
      actual.filter((path) => path.startsWith("/agent-showcase/ppt/previews/")),
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

  it("gives every showcase agent a dedicated localized composer placeholder", () => {
    const expected = {
      "course-video": {
        zh: "粘贴讲稿，或输入课程主题；也可以上传 docx、pdf、md 文稿",
        en: "Paste a script or topic; upload DOCX, PDF, or MD.",
      },
      "ppt-master": {
        zh: "输入 PPT 主题，或上传文档、现有 PPT",
        en: "Enter a topic, or upload a document or existing PPT.",
      },
      "podcast-studio": {
        zh: "输入主题、粘贴公开网页链接，或上传文档",
        en: "Enter a topic, paste a public URL, or upload a document.",
      },
      "creative-studio": {
        zh: "输入课程主题、核心知识点和想要的风格",
        en: "Enter a topic, key points, and desired style.",
      },
    } as const;

    for (const id of agentIds) {
      const placeholder = agent(id).inputPlaceholder;
      expect(placeholder, id).toEqual(expected[id]);
      // Agent-specific copy must not regress to the global composer sentence.
      expect(placeholder.zh, id).not.toContain("今天我能为你做些什么");
      expect(placeholder.en, id).not.toContain("How can I assist you today");
    }
  });

  it("ships hero art with a lossy alpha channel (corner alpha covered by E2E)", () => {
    // Node has no WebP decoder; the four-corner alpha-0 / no-white-card checks
    // run in the browser (tests/e2e/agent-new-chat-guide.spec.ts). Here we only
    // guard the binary contract: every hero must be an alpha WebP, not an
    // opaque rectangle re-exported as .webp.
    for (const id of agentIds) {
      const path = join(publicRoot, agent(id).character);
      const bytes = readFileSync(path);
      expect(bytes.subarray(0, 4).toString(), id).toBe("RIFF");
      expect(bytes.subarray(8, 12).toString(), id).toBe("WEBP");
      expect(bytes.includes(Buffer.from("VP8X")), id).toBe(true);
      expect(bytes.includes(Buffer.from("ALPH")), id).toBe(true);
      expect(statSync(path).size, id).toBeLessThanOrEqual(150_000);
    }
  });

  it("keeps the course-video review step logic explicit", () => {
    const step = agent("course-video").steps[1]!;
    expect(step.description.zh).toContain("若你提供讲稿");
    expect(step.description.zh).toContain("若只给主题");
    expect(step.description.zh).toContain("不改写不扩写");
    expect(step.description.en).toContain("Supplied scripts stay verbatim");
    expect(step.description.en).toContain("topic-only briefs");
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

  it("turns every case prompt into a reusable template without case facts", () => {
    const sourceLabels = agentIds.flatMap((id) =>
      agent(id).cases.flatMap((item) =>
        item.kind === "video" || item.kind === "podcast"
          ? (item.sources ?? []).flatMap((source) => [
              source.label.zh,
              source.label.en,
            ])
          : [],
      ),
    );
    const sourceNames = ["InfoQ", "经济参考报", "KubeCon", "新华社", "Mozilla"];

    for (const id of agentIds) {
      for (const item of agent(id).cases) {
        const where = `${id}/${item.id}`;
        for (const title of [item.title.zh, item.title.en]) {
          expect(item.prompt, `${where} leaks its own title`).not.toContain(
            title,
          );
        }
        expect(
          item.prompt,
          `${where} keeps sample durations and page counts out`,
        ).not.toMatch(/\d+(?:\.\d+)?\s*(?:秒|页|seconds?|slides?|pages?)/i);
        expect(item.prompt, `${where} has no URL`).not.toMatch(/https?:\/\//);
        expect(item.prompt, `${where} has no case media path`).not.toContain(
          "/agent-showcase/",
        );
        for (const label of [...sourceLabels, ...sourceNames]) {
          expect(item.prompt, `${where} leaks source ${label}`).not.toContain(
            label,
          );
        }
        if (item.kind === "deck") {
          expect(item.prompt, `${where} leaks the case brand`).not.toContain(
            "360",
          );
        }
        if (item.kind === "video") {
          // Sample lengths belong to the case, never to the new task.
          expect(item.prompt, `${where} leaks a sample length`).not.toMatch(
            /\b(?:32|29|39)\b/,
          );
        }
        if (item.kind === "podcast") {
          // The display transcript stays a case fact: none of its lines and
          // none of its figures or years reach the new task.
          expect(item.prompt, `${where} leaks the case year`).not.toContain(
            "2026",
          );
          for (const turn of item.transcript) {
            expect(
              item.prompt,
              `${where} leaks a transcript line`,
            ).not.toContain(turn.text.slice(0, 8));
          }
        }
      }
    }
  });

  it("gives every case a fill-only action label within the card budget", () => {
    for (const id of agentIds) {
      for (const item of agent(id).cases) {
        expect(item.actionLabel, `${id}/${item.id}`).toBeDefined();
        expect(item.actionLabel?.zh.trim(), `${id}/${item.id}`).not.toBe("");
        expect(item.actionLabel?.en.trim(), `${id}/${item.id}`).not.toBe("");
        if (item.kind === "video") {
          // The video card's bottom row shares space with a truncatable meta
          // column, so its label has a hard length budget.
          expect(item.actionLabel?.zh.length).toBeLessThanOrEqual(6);
          expect(item.actionLabel?.en.length).toBeLessThanOrEqual(16);
        }
      }
    }
  });

  it("uses request placeholders the composer can recognize", () => {
    // The composer selects the first fill-in placeholder of the inserted
    // segment: a 【请…】 / 【可选填写…】 token. Section headers share the
    // brackets, so every token must be either a known header or a placeholder.
    const headers = [
      "【我的内容】",
      "【风格与版式】",
      "【必需材料】",
      "【视觉与配音】",
      "【需填写】",
      "【可选】",
      "【原文来源】",
      "【固定音色】",
    ];
    const placeholder = /^【(?:请[^【】]*|可选填写[^【】]*)】$/;

    for (const id of agentIds) {
      for (const item of agent(id).cases) {
        const where = `${id}/${item.id}`;
        // A fixed-script video takes an uploaded script, so it may have no
        // text placeholder at all; every token it does use must be known.
        const tokens = item.prompt.match(/【[^【】\n]*】/g) ?? [];
        for (const token of tokens) {
          expect(
            headers.includes(token) || placeholder.test(token),
            `${where} uses an unknown token ${token}`,
          ).toBe(true);
        }
      }
    }
  });

  it("keeps mode lines off decks and videos", () => {
    for (const item of [
      ...agent("course-video").cases,
      ...agent("ppt-master").cases,
    ]) {
      expect(item.prompt.split("\n")[0]).not.toMatch(/^\[模式/);
    }
  });

  it("uses HTTPS for every external source", () => {
    const sources = agentIds.flatMap((id) =>
      agent(id).cases.flatMap((item) => {
        if (item.kind === "video" || item.kind === "podcast") {
          return item.sources ?? [];
        }
        return [];
      }),
    );
    expect(sources.length).toBeGreaterThan(0);
    for (const source of sources) {
      expect(new URL(source.url).protocol, source.url).toBe("https:");
    }
  });
});
