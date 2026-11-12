import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  type Mock,
  vi,
} from "vitest";

import type { HomeCourseItem } from "@/components/workspace/home-course-catalog";
import { HomeCourseEntry } from "@/components/workspace/home-course-entry";
import { translations } from "@/core/i18n/translations";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: translations["zh-CN"] }),
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let mediaQuery: {
  matches: boolean;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
};
let playSpy: ReturnType<typeof vi.spyOn>;
let pauseSpy: ReturnType<typeof vi.spyOn>;

function button(text: string): HTMLButtonElement {
  const result = Array.from(document.querySelectorAll("button")).find((item) =>
    item.textContent?.includes(text),
  );
  if (!result) throw new Error(`button not found: ${text}`);
  return result;
}

describe("HomeCourseEntry", () => {
  let container: HTMLDivElement;
  let root: Root;
  let onSelect: Mock<(item: HomeCourseItem) => void>;

  beforeEach(() => {
    mediaQuery = {
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => mediaQuery),
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    playSpy = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    pauseSpy = vi
      .spyOn(HTMLMediaElement.prototype, "pause")
      .mockImplementation(() => undefined);
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

  it("defaults to video course and exposes three cards", async () => {
    await render();
    expect(button("视频课").getAttribute("aria-expanded")).toBe("true");
    expect(button("图文音频课").getAttribute("aria-expanded")).toBe("false");
    for (const title of ["文字动效", "图片动效", "AI视频"]) {
      expect(button(title)).toBeDefined();
    }
    expect(document.querySelector("#home-course-panel-video")).not.toBeNull();
    expect(document.querySelector("#home-course-panel-text")).toBeNull();
  });

  it("uses unified AI video badges and text animation copy in both locales", () => {
    for (const locale of ["zh-CN", "en-US"] as const) {
      const items = translations[locale].workspace.home.courseItems;
      const expectedBadge = locale === "zh-CN" ? "AI视频" : "AI Video";
      expect(items["course-video"].badge).toBe(expectedBadge);
      expect(items.ai_image.badge).toBe(expectedBadge);
      expect(items.ai_video.badge).toBe(expectedBadge);
      expect(items["course-video"].title).toBe(
        locale === "zh-CN" ? "文字动效" : "Text animation",
      );
      expect(items["course-video"].mediaAlt).not.toContain("Remotion");
    }
    expect(
      translations["zh-CN"].agents.newChatGuides["course-video"].displayName,
    ).toBe("课程视频");
    expect(
      translations["en-US"].agents.newChatGuides["course-video"].displayName,
    ).toBe("Course video");
  });

  it("uses demo-style icons arrows and real artwork for text cards", async () => {
    await render();
    expect(button("视频课").querySelector("svg")).not.toBeNull();
    expect(
      button("文字动效").querySelector("[data-testid='creation-arrow'] svg"),
    ).not.toBeNull();
    await act(async () => button("图文音频课").click());
    for (const [title, src] of [
      ["PPT制作", "/course-samples/ppt-work.webp"],
      ["播客制作", "/course-samples/podcast-work.webp"],
    ] as const) {
      const card = button(title);
      expect(card.querySelector("img")?.getAttribute("src")).toBe(src);
      expect(
        card.querySelector("[data-testid='creation-arrow']"),
      ).not.toBeNull();
    }
  });

  it("switches to text course and pauses hidden panel video", async () => {
    await render();
    expect(playSpy).toHaveBeenCalledTimes(3);
    await act(async () => button("图文音频课").click());
    for (const title of ["海报制作", "PPT制作", "播客制作"]) {
      expect(button(title)).toBeDefined();
    }
    expect(document.querySelector("#home-course-panel-video")).toBeNull();
    expect(pauseSpy).toHaveBeenCalledTimes(3);
    await act(async () => button("视频课").click());
    expect(document.querySelector("#home-course-panel-text")).toBeNull();
    expect(playSpy).toHaveBeenCalledTimes(6);
  });

  it("reduced motion never autoplays and change event pauses current video", async () => {
    mediaQuery.matches = true;
    await render();
    expect(playSpy).not.toHaveBeenCalled();
    pauseSpy.mockClear();

    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(pauseSpy).toHaveBeenCalledTimes(3);

    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    mediaQuery.matches = false;
    await act(async () => {
      mediaQuery.addEventListener.mock.calls[0]?.[1]?.(new Event("change"));
    });
    expect(playSpy).toHaveBeenCalledTimes(3);

    mediaQuery.matches = true;
    await act(async () => {
      mediaQuery.addEventListener.mock.calls[0]?.[1]?.(new Event("change"));
    });
    expect(pauseSpy).toHaveBeenCalled();
  });

  it("ignores play interruption caused by pause", async () => {
    playSpy.mockRejectedValueOnce(
      new DOMException("interrupted by pause", "AbortError"),
    );
    await render();
    expect(document.body.textContent).not.toContain("视频暂时无法播放");
  });

  it("clears a previous failure after media reaches playing", async () => {
    const video = document.querySelector("video");
    expect(video).toBeNull();
    playSpy.mockRejectedValue(new Error("not allowed"));
    await render();
    expect(document.body.textContent).toContain("视频暂时无法播放");
    const current = document.querySelector("video");
    await act(async () =>
      current!.dispatchEvent(new Event("playing", { bubbles: true })),
    );
    const card = current!.closest("[data-course-entry]");
    expect(card?.textContent).not.toContain("视频暂时无法播放");
  });

  it("play rejection and media error show fallback without disabling entry", async () => {
    playSpy.mockRejectedValue(new Error("not allowed"));
    await render();
    const video = document.querySelector("video");
    expect(video).not.toBeNull();
    await act(async () => video!.dispatchEvent(new Event("error")));
    expect(document.body.textContent).toContain("视频暂时无法播放");
    await act(async () => button("文字动效").click());
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0]?.[0].id).toBe("course-video");
  });
});
