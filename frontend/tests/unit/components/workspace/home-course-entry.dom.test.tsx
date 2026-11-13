import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HomeCourseItem } from "@/components/workspace/home-course-catalog";
import { HomeCourseEntry } from "@/components/workspace/home-course-entry";
import { translations } from "@/core/i18n/translations";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: translations["zh-CN"] }),
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const EXPECTED_TAGS = [
  ["主题变科普视频", "截图变产品教程", "讲稿原样转视频"],
  ["等距信息图", "白板手绘", "商务实景"],
  ["纸艺剧场", "3D角色", "商务实景"],
  ["要点变知识卡", "课程变竖版海报", "知识变连续漫画"],
  ["主题快速成课件", "旧PPT局部修改", "文字不变做美化"],
  ["原文朗读", "单人新闻", "双人访谈", "双人辩论"],
] as const;

const IDS = [
  "course-video",
  "ai_image",
  "ai_video",
  "creative-studio",
  "ppt-master",
  "podcast-studio",
] as const;

describe("HomeCourseEntry", () => {
  let container: HTMLDivElement;
  let root: Root;
  let onSelect: ReturnType<typeof vi.fn<(item: HomeCourseItem) => void>>;

  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    onSelect = vi.fn();
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  async function render() {
    await act(async () => {
      root.render(<HomeCourseEntry onSelect={onSelect} />);
    });
  }

  function entry(id: string): HTMLButtonElement {
    const result = container.querySelector<HTMLButtonElement>(
      `[data-course-entry='${id}']`,
    );
    if (!result) throw new Error(`entry not found: ${id}`);
    return result;
  }

  it("renders two named groups without a count and keeps three videos mounted", async () => {
    await render();

    expect(container.querySelectorAll("[data-course-group]")).toHaveLength(2);
    const text = container.textContent ?? "";
    for (const copy of [
      "视频课",
      "VIDEO CREATION",
      "图文音频课",
      "DESIGN & AUDIO",
    ]) {
      expect(text).toContain(copy);
    }
    expect(text).not.toContain("03");
    expect(container.querySelectorAll("[data-course-entry]")).toHaveLength(6);
    expect(container.querySelectorAll("video")).toHaveLength(3);
  });

  it("uses final names and assistant-toned media labels without status or captions", async () => {
    await render();

    const titles = [
      "文字动效",
      "图片动效",
      "AI视频",
      "课程海报",
      "PPT课件",
      "语音播客",
    ];
    const mediaLabels = [
      "文稿→动态讲解",
      "文稿→动态图片",
      "文稿→动态镜头",
      "主题→视觉海报",
      "资料→演示文稿",
      "资料→语音播客",
    ];
    IDS.forEach((id, index) => {
      const card = entry(id);
      expect(card.textContent).toContain(titles[index]);
      expect(card.textContent).toContain(mediaLabels[index]);
      expect(card.querySelector("[data-media-status]")).toBeNull();
      expect(card.querySelector("[data-media-caption]")).toBeNull();
      const label = card.querySelector("[data-media-label]")!;
      expect(label.className).toContain("bg-white/90");
      expect(label.className).toContain("text-slate-600");
      for (const tag of card.querySelectorAll("[data-course-tag]")) {
        expect(tag.className).toContain("bg-violet-50");
        expect(tag.className).toContain("text-violet-500");
      }
    });
  });

  it("renders exactly the approved 3/3/3/3/3/4 tags and removes rejected copy", async () => {
    await render();

    IDS.forEach((id, index) => {
      const tags = Array.from(
        entry(id).querySelectorAll("[data-course-tag]"),
      ).map((tag) => tag.textContent);
      expect(tags).toEqual(EXPECTED_TAGS[index]);
    });
    const text = container.textContent ?? "";
    for (const removed of [
      "数据变动态图表",
      "流程变信息图",
      "文档精编成课件",
      "精密工程",
      "纸艺拼贴",
      "Remotion",
      "TTS",
      "研究写作",
      "原生PPTX",
      "原生 PPTX",
    ]) {
      expect(text).not.toContain(removed);
    }
  });

  it("uses the approved static artwork", async () => {
    await render();

    for (const [id, src] of [
      ["creative-studio", "/course-samples/poster-design.svg"],
      ["ppt-master", "/course-samples/ppt-design.svg"],
      ["podcast-studio", "/course-samples/podcast-work.webp"],
    ] as const) {
      expect(entry(id).querySelector("img")?.getAttribute("src")).toBe(src);
    }
  });
});
