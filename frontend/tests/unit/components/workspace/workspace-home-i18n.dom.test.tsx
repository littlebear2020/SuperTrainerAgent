import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkspaceHome from "@/components/workspace/workspace-home";
import { I18nProvider } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";
import { zhCN } from "@/core/i18n/locales/zh-CN";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));
vi.mock("@/core/threads/hooks", () => ({
  useThreads: () => ({ data: [] }),
}));
vi.mock("@/components/workspace/project-list", () => ({
  ProjectList: () => <div data-testid="project-list" />,
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const CJK = /[一-鿿]/;
const IDS = [
  "course-video",
  "ai_image",
  "ai_video",
  "creative-studio",
  "ppt-master",
  "podcast-studio",
] as const;

const TAGS = {
  "zh-CN": [
    ["主题变科普视频", "截图变产品教程", "讲稿原样转视频"],
    ["等距信息图", "白板手绘", "商务实景"],
    ["纸艺剧场", "3D角色", "商务实景"],
    ["要点变知识卡", "课程变竖版海报", "知识变连续漫画"],
    ["主题快速成课件", "旧PPT局部修改", "文字不变做美化"],
    ["原文朗读", "单人新闻", "双人访谈", "双人辩论"],
  ],
  "en-US": [
    [
      "Topic → explainer video",
      "Screenshots → product tutorial",
      "Script → video, unchanged",
    ],
    ["Isometric infographics", "Whiteboard sketch", "Business footage"],
    ["Paper theater", "3D characters", "Business footage"],
    [
      "Key points → knowledge cards",
      "Course → vertical poster",
      "Knowledge → comic series",
    ],
    [
      "Topic → quick slides",
      "Edit part of an old deck",
      "Restyle, keep text unchanged",
    ],
    [
      "Read the original text",
      "Solo news",
      "Two-person interview",
      "Two-person debate",
    ],
  ],
} as const;

describe("workspace-home final i18n and teacher hero", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderHome(locale: "en-US" | "zh-CN") {
    document.cookie = `locale=${locale}; path=/`;
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <I18nProvider initialLocale={locale}>
            <WorkspaceHome />
          </I18nProvider>
        </QueryClientProvider>,
      );
    });
  }

  it("renders the English hero, groups, teacher and all final tags without CJK", async () => {
    await renderHome("en-US");
    const text = container.textContent ?? "";
    expect(text).toContain("Great content starts with one idea.");
    expect(text).toContain("Video courses");
    expect(text).toContain("VIDEO CREATION");
    expect(text).toContain("Design & audio courses");
    expect(text).toContain("DESIGN & AUDIO");
    expect(text).toContain("Your AI creative assistant");
    const teacherAlt =
      "Smiling young Asian male cartoon teacher holding a book and pointer";
    const teacher = container.querySelector(`img[alt="${teacherAlt}"]`);
    expect(teacher?.getAttribute("src")).toBe(
      "/course-samples/teacher-A-half.png",
    );

    IDS.forEach((id, index) => {
      const copy = enUS.workspace.home.courseItems[id];
      expect(text).toContain(copy.title);
      expect(text).toContain(copy.description);
      expect(text).toContain(copy.mediaLabel);
      const expectedTags = TAGS["en-US"][index];
      if (!expectedTags) throw new Error(`missing English tags for ${id}`);
      expect(copy.tags).toEqual(expectedTags);
      for (const tag of expectedTags) {
        expect(text).toContain(tag);
      }
    });
    expect(text).not.toContain("Recent Projects");
    expect(CJK.test(text), `en-US contains CJK: ${text}`).toBe(false);
  });

  it("renders the Chinese hero, groups, teacher and exact final tags", async () => {
    await renderHome("zh-CN");
    const text = container.textContent ?? "";
    expect(text).toContain("让好内容，从一个想法开始");
    expect(text).toContain("视频课");
    expect(text).toContain("VIDEO CREATION");
    expect(text).toContain("图文音频课");
    expect(text).toContain("DESIGN & AUDIO");
    expect(text).toContain("你的 AI 创作助教");
    const teacherAlt = "手持书本和教鞭、面带微笑的卡通亚洲年轻男老师";
    const teacher = container.querySelector(`img[alt="${teacherAlt}"]`);
    expect(teacher?.getAttribute("src")).toBe(
      "/course-samples/teacher-A-half.png",
    );

    IDS.forEach((id, index) => {
      const copy = zhCN.workspace.home.courseItems[id];
      expect(text).toContain(copy.title);
      expect(text).toContain(copy.description);
      expect(text).toContain(copy.mediaLabel);
      const expectedTags = TAGS["zh-CN"][index];
      if (!expectedTags) throw new Error(`missing Chinese tags for ${id}`);
      expect(copy.tags).toEqual(expectedTags);
      for (const tag of expectedTags) {
        expect(text).toContain(tag);
      }
    });
    expect(text).not.toContain("近期项目");
  });
});
